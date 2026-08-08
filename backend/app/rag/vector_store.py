"""Minimal in-memory vector store: TF-IDF vectors with cosine similarity.

Written by hand in pure Python rather than pulling in scikit-learn or a vector
database. For a corpus of a few dozen curated clinical passages the numerics
are identical and the dependency and infrastructure cost is zero, which is the
right trade for a 36-hour MVP. The interface (`add`, `search`) is the same
shape a real vector DB exposes, so swapping in pgvector later is a change
behind this file rather than through the application.

Tokenisation is Unicode-aware so Devanagari and other Indic terms index
alongside English ones.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

_TOKEN_RE = re.compile(r"[\wऀ-෿]+", re.UNICODE)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "is", "are",
    "was", "were", "be", "been", "on", "at", "by", "as", "that", "this", "it",
    "from", "not", "no", "but", "if", "then", "than", "so", "such", "may",
    "hai", "hain", "ka", "ki", "ke", "me", "mein", "se", "ko", "aur", "bhi",
}


def tokenize(text: str) -> List[str]:
    if not text:
        return []
    folded = unicodedata.normalize("NFKC", text).lower()
    return [
        tok for tok in _TOKEN_RE.findall(folded)
        if tok not in _STOPWORDS and len(tok) > 1
    ]


@dataclass
class Document:
    doc_id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    term_freq: Dict[str, float] = field(default_factory=dict, repr=False)


@dataclass
class SearchHit:
    document: Document
    score: float

    @property
    def metadata(self) -> Dict[str, Any]:
        return self.document.metadata


class VectorStore:
    """TF-IDF index over a small document set.

    IDF is recomputed lazily on first search after a mutation, so bulk loading
    at startup costs one pass rather than one per document.
    """

    def __init__(self) -> None:
        self._documents: List[Document] = []
        self._idf: Dict[str, float] = {}
        self._norms: Dict[str, float] = {}
        self._dirty = True

    def __len__(self) -> int:
        return len(self._documents)

    def add(self, doc_id: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        tokens = tokenize(text)
        counts = Counter(tokens)
        total = float(sum(counts.values())) or 1.0
        self._documents.append(
            Document(
                doc_id=doc_id,
                text=text,
                metadata=metadata or {},
                term_freq={term: n / total for term, n in counts.items()},
            )
        )
        self._dirty = True

    def add_many(self, entries: Iterable[Tuple[str, str, Dict[str, Any]]]) -> None:
        for doc_id, text, metadata in entries:
            self.add(doc_id, text, metadata)

    def _reindex(self) -> None:
        n_docs = len(self._documents)
        if n_docs == 0:
            self._idf, self._norms, self._dirty = {}, {}, False
            return

        doc_freq: Counter = Counter()
        for doc in self._documents:
            doc_freq.update(doc.term_freq.keys())

        # Smoothed IDF: never zero, so a term present in every document still
        # contributes a little rather than vanishing.
        self._idf = {
            term: math.log((1 + n_docs) / (1 + df)) + 1.0 for term, df in doc_freq.items()
        }

        self._norms = {}
        for doc in self._documents:
            total = sum(
                (tf * self._idf.get(term, 0.0)) ** 2 for term, tf in doc.term_freq.items()
            )
            self._norms[doc.doc_id] = math.sqrt(total) or 1.0

        self._dirty = False

    def search(self, query: str, top_k: int = 3, min_score: float = 0.0) -> List[SearchHit]:
        if self._dirty:
            self._reindex()
        if not self._documents:
            return []

        query_counts = Counter(tokenize(query))
        if not query_counts:
            return []

        total = float(sum(query_counts.values()))
        query_vec = {
            term: (count / total) * self._idf.get(term, 0.0)
            for term, count in query_counts.items()
        }
        query_norm = math.sqrt(sum(v * v for v in query_vec.values())) or 1.0

        hits: List[SearchHit] = []
        for doc in self._documents:
            dot = 0.0
            # Iterate the shorter side; queries are short, documents are not.
            for term, q_weight in query_vec.items():
                tf = doc.term_freq.get(term)
                if tf:
                    dot += q_weight * tf * self._idf.get(term, 0.0)
            if dot <= 0.0:
                continue
            score = dot / (query_norm * self._norms[doc.doc_id])
            if score >= min_score:
                hits.append(SearchHit(document=doc, score=score))

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
