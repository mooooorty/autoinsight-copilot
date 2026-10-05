from __future__ import annotations

import re
from collections import Counter

import jieba
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .models import Chunk, Hit

STOPWORDS = {"的", "了", "和", "与", "是", "有", "吗", "呢", "多少", "什么", "如何", "是否", "请问", "哪个", "哪些", "可以", "支持", "目前", "关于", "这款", "该车", "车型", "介绍", "说明", "一下", "分别", "几个", "在哪儿", "哪里", "操作"}


def tokens(text: str) -> list[str]:
    words = [part.lower().strip() for part in jieba.cut(text) if part.strip()]
    return [word for word in words if word not in STOPWORDS and re.search(r"[\w\u4e00-\u9fff]", word)]


class SearchIndex:
    def __init__(self, chunks: list[Chunk]) -> None:
        if not chunks:
            raise ValueError("至少需要一个非空资料片段")
        self.chunks = tuple(chunks)
        self.token_sets = [set(tokens(f"{chunk.location} {chunk.text}")) for chunk in chunks]
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform(f"{chunk.location} {chunk.text}" for chunk in chunks)

    def search(self, question: str, mode: str = "hybrid", top_k: int = 5) -> list[Hit]:
        if mode not in {"keyword", "hybrid"}:
            raise ValueError(f"Unknown retrieval mode: {mode}")
        question = question.strip()
        if not question:
            return []
        query_tokens = set(tokens(question))
        vector = cosine_similarity(self.vectorizer.transform([question]), self.matrix)[0]
        hits: list[Hit] = []
        for chunk, chunk_tokens, vector_score in zip(self.chunks, self.token_sets, vector):
            overlap = len(query_tokens & chunk_tokens)
            keyword_score = overlap / max(len(query_tokens), 1)
            score = keyword_score if mode == "keyword" else 0.45 * keyword_score + 0.55 * float(vector_score)
            if score > 0:
                hits.append(Hit(chunk, round(score, 6), round(keyword_score, 6), round(float(vector_score), 6)))
        return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.source, hit.chunk.location))[:top_k]


def query_terms(question: str) -> set[str]:
    """Content terms for transparent local coverage checks."""
    return {word for word in tokens(question) if len(word) > 1 or word.isascii()}
