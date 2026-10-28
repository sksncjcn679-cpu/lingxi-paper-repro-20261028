#!/usr/bin/env python3
"""Train OS-domain distributional word vectors (PPMI + truncated SVD) from local documentation.

fable13.  Reproducible and offline: the corpus is whatever system/Python documentation exists on
the build host (C headers, Python stdlib sources and docstrings, info pages, package docs).
Hyperparameters are fixed a priori from Levy, Goldberg & Dagan (2015), *Improving
Distributional Similarity with Lessons Learned from Word Embeddings*: symmetric window 5 with
harmonic 1/d weighting, PPMI with context-distribution smoothing alpha=0.75, SVD with
eigenvalue weighting p=0.5, 128 dimensions.  Nothing is tuned on any evaluation set, and the
project's own sources/benchmarks are never part of the corpus.

    python scripts/train_os_word_vectors.py --out src/agent_runtime/providers/data/os_wordvec.npz
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import re
import sys
import time
from collections import Counter

import numpy as np

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]+")
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
SOURCES = [
    ("/usr/include/**/*.h", 40_000_000),
    ("/usr/lib/python3*/**/*.py", 25_000_000),
    ("/usr/lib/python3/dist-packages/**/*.py", 15_000_000),
    ("/usr/share/info/*.gz", 5_000_000),
    ("/usr/share/doc/**/*.gz", 5_000_000),
]
EXCLUDE = ("/test", "/tests/", "agent_runtime", "fable")


_BOILERPLATE = re.compile(r"(?i)copyright|license|licence|warranty|merchantability|gnu |gpl|liable|redistribut|hereby|disclaim")


def words(text: str) -> list[str]:
    # A-priori boilerplate removal: licence headers dominate C/Python files and would otherwise
    # make e.g. "permission"/"library" legal terms.
    text = "\n".join(line for line in text.splitlines() if not _BOILERPLATE.search(line))
    out = []
    for tok in TOKEN.findall(text):
        for part in CAMEL.sub(" ", tok).split():
            for p in part.split("_"):
                if len(p) >= 2:
                    out.append(p.lower())
    return out


def corpus_files():
    for pattern, budget in SOURCES:
        used = 0
        for path in sorted(glob.glob(pattern, recursive=True)):
            if any(x in path for x in EXCLUDE) or not os.path.isfile(path):
                continue
            try:
                raw = gzip.open(path, "rb").read() if path.endswith(".gz") else open(path, "rb").read()
            except (OSError, EOFError):
                continue
            used += len(raw)
            yield path, raw.decode("utf-8", "ignore")
            if used >= budget:
                break


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--vocab", type=int, default=30000)
    ap.add_argument("--dim", type=int, default=128)
    ap.add_argument("--window", type=int, default=5)
    args = ap.parse_args()
    t0 = time.time()
    docs = []
    freq: Counter = Counter()
    nfiles = 0
    for _path, text in corpus_files():
        toks = words(text)
        if toks:
            docs.append(toks)
            freq.update(toks)
            nfiles += 1
    total = sum(freq.values())
    vocab = [w for w, c in freq.most_common(args.vocab) if c >= 10]
    index = {w: i for i, w in enumerate(vocab)}
    V = len(vocab)
    print(f"files={nfiles} tokens={total} vocab={V} t={time.time()-t0:.0f}s", file=sys.stderr)
    from scipy import sparse

    cooc = sparse.csr_matrix((V, V), dtype=np.float32)
    buf_r, buf_c, buf_w = [], [], []

    def flush():
        nonlocal cooc, buf_r, buf_c, buf_w
        if buf_r:
            r = np.concatenate(buf_r); c = np.concatenate(buf_c); w = np.concatenate(buf_w)
            cooc = cooc + sparse.coo_matrix((w, (r, c)), shape=(V, V)).tocsr()
            buf_r, buf_c, buf_w = [], [], []

    pending = 0
    for toks in docs:
        ids = np.fromiter((index.get(t, -1) for t in toks), dtype=np.int64, count=len(toks))
        for d in range(1, args.window + 1):
            a, b = ids[:-d], ids[d:]
            m = (a >= 0) & (b >= 0)
            if not m.any():
                continue
            a, b = a[m], b[m]
            w = np.full(a.shape, 1.0 / d, dtype=np.float32)
            buf_r += [a, b]; buf_c += [b, a]; buf_w += [w, w]
            pending += 2 * a.size
        if pending > 20_000_000:
            flush(); pending = 0
    flush()
    print(f"cooc nnz={cooc.nnz} t={time.time()-t0:.0f}s", file=sys.stderr)
    # PPMI with context-distribution smoothing (alpha = 0.75)
    row = np.asarray(cooc.sum(axis=1)).ravel()
    col = np.asarray(cooc.sum(axis=0)).ravel() ** 0.75
    total_w = row.sum()
    cooc = cooc.tocoo()
    pmi = np.log((cooc.data * col.sum()) / (row[cooc.row] * col[cooc.col]))
    keep = pmi > 0
    ppmi = sparse.csr_matrix((pmi[keep].astype(np.float32), (cooc.row[keep], cooc.col[keep])), shape=(V, V))
    from sklearn.decomposition import TruncatedSVD

    svd = TruncatedSVD(n_components=args.dim, algorithm="randomized", n_iter=5, random_state=0)
    U = svd.fit_transform(ppmi)  # = U * Sigma
    sigma = svd.singular_values_
    vecs = U / np.sqrt(np.maximum(sigma, 1e-9))  # eigenvalue weighting p = 0.5 -> U * Sigma^0.5
    vecs /= np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)
    scale = np.abs(vecs).max(axis=1, keepdims=True) / 127.0
    q = np.clip(np.rint(vecs / scale), -127, 127).astype(np.int8)
    prob = np.array([freq[w] / total for w in vocab], dtype=np.float32)
    # SIF common component (Arora, Liang & Ma 2017): first principal component of SIF-weighted
    # averages of 20-token windows sampled from the corpus; removed from every sentence vector.
    rng = np.random.default_rng(0)
    deq = q.astype(np.float32) * scale
    sif_w = 1e-3 / (1e-3 + prob)
    samples = []
    for _ in range(20000):
        toks = docs[rng.integers(len(docs))]
        if len(toks) < 20:
            continue
        st = rng.integers(len(toks) - 19)
        ids = [index[t] for t in toks[st:st + 20] if t in index]
        if ids:
            samples.append((deq[ids] * sif_w[ids, None]).mean(axis=0))
    S = np.stack(samples)
    _, _, vt = np.linalg.svd(S - 0 * S.mean(axis=0), full_matrices=False)
    pc = vt[0].astype(np.float32)
    meta = {"files": nfiles, "tokens": int(total), "vocab": V, "dim": args.dim, "window": args.window,
            "method": "PPMI(alpha=0.75)+TruncatedSVD, eigenvalue weighting p=0.5 (Levy et al. 2015)",
            "sources": [s for s, _ in SOURCES], "seconds": round(time.time() - t0, 1)}
    np.savez_compressed(args.out, vocab=np.array(vocab), q=q, scale=scale.astype(np.float32).ravel(),
                        prob=prob, pc=pc, meta=json.dumps(meta))
    print(json.dumps(meta), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
