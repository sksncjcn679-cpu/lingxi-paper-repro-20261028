"""fable13: ``docsem`` embedding -- real (non-mock) semantic vectors without a network or GPU.

Word vectors are learned offline from the host's own system documentation (C headers, Python
standard-library sources and docstrings, info pages, package docs) with PPMI + truncated SVD
(``scripts/train_os_word_vectors.py``; hyperparameters fixed a priori from Levy, Goldberg &
Dagan 2015).  A text is embedded as a SIF sentence vector (Arora, Liang & Ma 2017: smooth
inverse-frequency weights a/(a+p(w)), common component removed), concatenated with the
CJK-aware lexical features (character n-grams, bilingual concept normalisation) so that exact
identifiers, numbers and Chinese text still match.  Both halves are unit-normalised and weighted
equally (fixed a priori), so cosine = 0.5 * semantic + 0.5 * lexical.

The shipped model file (``providers/data/os_wordvec.npz``) holds int8 vectors (~2.6 MB).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from agent_runtime.providers.lexical_embedding import LexicalEmbedding

_MODEL = Path(__file__).with_name("data") / "os_wordvec.npz"
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SIF_A = 1e-3
_SEM_WEIGHT = 0.5
_SHARED_CACHE: dict[int, dict[str, np.ndarray]] = {}


def _words(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(text or ""):
        for part in _CAMEL.sub(" ", tok).split():
            for p in part.split("_"):
                if len(p) >= 2:
                    out.append(p.lower())
    return out


@lru_cache(maxsize=1)
def _load() -> tuple[
    dict[str, int], np.ndarray, np.ndarray, np.ndarray, dict, np.ndarray
]:
    data = np.load(_MODEL, allow_pickle=False)
    vocab = [str(w) for w in data["vocab"]]
    vecs = data["q"].astype(np.float32) * data["scale"][:, None]
    prob = data["prob"]
    sif = (_SIF_A / (_SIF_A + prob)).astype(np.float32)
    pc = data["pc"].astype(np.float32)
    pc /= max(float(np.linalg.norm(pc)), 1e-9)
    return (
        {w: i for i, w in enumerate(vocab)},
        vecs,
        sif,
        pc,
        json.loads(str(data["meta"])),
        prob,
    )


@lru_cache(maxsize=1)
def _metadata() -> dict:
    # Text-only cooperation needs dimensions, but never consumes semantic vectors.
    with np.load(_MODEL, allow_pickle=False) as data:
        return json.loads(str(data["meta"]))


def model_info() -> dict:
    return dict(_metadata())


@lru_cache(maxsize=1)
def compression_dictionary_bytes(max_bytes: int = 32768) -> bytes:
    """Return the model-derived preset dictionary without reopening the model archive."""
    if max_bytes <= 0:
        raise ValueError("compression dictionary size must be positive")
    index, _vecs, _sif, _pc, _meta, prob = _load()
    vocab: list[str | None] = [None] * len(index)
    for word, position in index.items():
        vocab[position] = word
    order = np.argsort(-prob, kind="stable")
    words, size = [], 0
    for i in order:
        word = vocab[int(i)]
        assert word is not None
        if size + len(word) + 1 > max_bytes:
            break
        words.append(word)
        size += len(word) + 1
    return " ".join(reversed(words)).encode("ascii", "ignore")


class DocSemEmbedding:
    """Hybrid semantic (SIF over documentation-trained vectors) + lexical embedding."""

    name = "docsem-embedding"

    def __init__(self, dim: int = 384, lexical_dim: int | None = None) -> None:
        self.sem_dim = int(model_info()["dim"])
        self.lex = LexicalEmbedding(dim=lexical_dim or max(64, int(dim) - self.sem_dim))
        self.dim = self.sem_dim + self.lex.dim
        # fable14: process-wide cache keyed by dimension -- operand names, plan texts and memory
        # summaries recur across tasks; vectors are immutable (returned copies are read-only).
        self._cache = _SHARED_CACHE.setdefault(self.dim, {})

    def _semantic(self, text: str) -> np.ndarray:
        index, vecs, sif, pc, _, _ = _load()
        ids = [index[w] for w in _words(text) if w in index]
        if not ids:
            return np.zeros(self.sem_dim, dtype=np.float32)
        v = (vecs[ids] * sif[ids, None]).mean(axis=0)
        v = v - pc * float(v @ pc)
        n = float(np.linalg.norm(v))
        return (v / n).astype(np.float32) if n > 0 else v.astype(np.float32)

    def _embed_one(self, text: str) -> np.ndarray:
        hit = self._cache.get(text)
        if hit is not None:
            return hit
        sem = self._semantic(text)
        lex = np.asarray(self.lex.embed([text])[0], dtype=np.float32)
        ln = float(np.linalg.norm(lex))
        lex = lex / ln if ln > 0 else lex
        out = np.concatenate([sem * np.sqrt(_SEM_WEIGHT), lex * np.sqrt(1 - _SEM_WEIGHT)]).astype(np.float32)
        n = float(np.linalg.norm(out))
        out = out / n if n > 0 else out
        out.setflags(write=False)
        if len(self._cache) < 16384:
            self._cache[text] = out
        return out

    def embed(self, texts):
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._embed_one(str(t)) for t in texts])


__all__ = ["DocSemEmbedding", "compression_dictionary_bytes", "model_info"]
