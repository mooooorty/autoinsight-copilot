from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    location: str
    text: str

    @property
    def citation(self) -> str:
        return f"{self.source} · {self.location}"


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    keyword_score: float
    vector_score: float


@dataclass(frozen=True)
class Decision:
    relevant: bool
    covered: bool
    score: float
    provider: str
    reason: str


@dataclass(frozen=True)
class Evidence:
    hit: Hit
    decision: Decision


@dataclass(frozen=True)
class Answer:
    text: str
    abstained: bool
    citations: tuple[Chunk, ...]
    candidates: tuple[Evidence, ...]
    retrieval_mode: str
    decision_provider: str
    generation_provider: str
    latency_ms: float
    usage: dict[str, int | float] = field(default_factory=dict)
