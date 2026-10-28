"""Dependency-free BM25 and fixed reciprocal-rank fusion; no query-specific tuning."""
from __future__ import annotations
from collections import Counter
import math
import re

def tokens(text):
    return re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', str(text).lower())

def scores(query, documents, k1=1.2, b=0.75):
    docs = [Counter(tokens(d)) for d in documents]
    terms = set(tokens(query))
    avg = sum(sum(d.values()) for d in docs) / max(1, len(docs))
    out = []
    for doc in docs:
        total = 0.0
        length = sum(doc.values())
        for term in terms:
            df = sum(term in d for d in docs)
            idf = math.log(1 + (len(docs) - df + 0.5) / (df + 0.5))
            tf = doc.get(term, 0)
            total += idf * tf * (k1 + 1) / max(1e-9, tf + k1 * (1 - b + b * length / max(avg, 1e-9)))
        out.append(total)
    return out

def fused_order(semantic, lexical, constant=60):
    n = len(semantic)
    a = sorted(range(n), key=lambda i: (-semantic[i], i))
    c = sorted(range(n), key=lambda i: (-lexical[i], i))
    ar = {i: r for r, i in enumerate(a, 1)}
    cr = {i: r for r, i in enumerate(c, 1)}
    return sorted(range(n), key=lambda i: (-(1/(constant+ar[i]) + 1/(constant+cr[i])), i))
