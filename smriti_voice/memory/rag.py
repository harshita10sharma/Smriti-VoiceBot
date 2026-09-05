"""Retrieval over *unstructured* personal memories only.

Structured facts (who is my daughter, what medicine at 8am) are answered with
SQL through tools — a vector database would only add a hallucination surface to
questions that have an exact answer.  RAG is used where the data really is prose:
family stories, caregiver notes, memory descriptions.

The default retriever is lexical (BM25-style scoring over normalised tokens).  It
needs no model, no download and no network, so it works identically offline.  An
:class:`EmbeddingProvider` hook is defined for a future semantic upgrade, but
nothing here pretends to be an embedding search today.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from ..normalize import normalize_text
from .models import PersonalMemory

# Very common words carry no retrieval signal in short elderly-care queries.
STOPWORDS = frozenset("""
a an the is are was were be been being do does did of to in on at for with about my me i
you your and or if then than that this these those what which who whom when where why how
tell say said please can could would should will shall have has had it its as from by
""".split())


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Not implemented today.  Defined so a semantic retriever can be dropped in."""
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass
class RetrievedMemory:
    memory: PersonalMemory
    score: float
    matched_terms: list[str]


# Suffixes stripped from ASCII tokens only.  Indic scripts are left untouched:
# naive suffix stripping there would corrupt words rather than normalise them.
_SUFFIXES = ('ing', 'ies', 'ed', 'es', 's')


def _stem(token: str) -> str:
    if not token.isascii():
        return token
    for suffix in _SUFFIXES:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            stem = token[: -len(suffix)]
            return stem + 'y' if suffix == 'ies' else stem
    return token


def _terms(text: str) -> list[str]:
    return [_stem(token) for token in normalize_text(text).split()
            if token and token not in STOPWORDS and len(token) > 1]


class LexicalMemoryRetriever:
    """BM25-lite ranking.  Deterministic, explainable, offline."""

    def __init__(self, k1: float = 1.4, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    def search(self, query: str, memories: Sequence[PersonalMemory], *,
               limit: int = 3, min_score: float = 0.15) -> list[RetrievedMemory]:
        query_terms = _terms(query)
        if not query_terms or not memories:
            return []

        documents = [_terms(f'{m.title} {m.content} {m.people or ""} {m.category}') for m in memories]
        lengths = [len(doc) or 1 for doc in documents]
        average_length = sum(lengths) / len(lengths)
        document_frequency = Counter(term for doc in documents for term in set(doc))
        total = len(documents)

        results: list[RetrievedMemory] = []
        for memory, doc, length in zip(memories, documents, lengths):
            counts = Counter(doc)
            score = 0.0
            matched: list[str] = []
            for term in query_terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                matched.append(term)
                idf = math.log(1 + (total - document_frequency[term] + 0.5) /
                               (document_frequency[term] + 0.5))
                denominator = frequency + self.k1 * (1 - self.b + self.b * length / average_length)
                score += idf * (frequency * (self.k1 + 1)) / denominator
            if matched:
                # Normalise by query length so scores are comparable across queries.
                results.append(RetrievedMemory(memory, score / len(query_terms), matched))

        results.sort(key=lambda item: item.score, reverse=True)
        return [item for item in results if item.score >= min_score][:limit]
