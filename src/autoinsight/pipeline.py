from __future__ import annotations

from time import perf_counter

from .decisions import DecisionProvider, LocalRules, named_entities
from .generation import ExtractiveGenerator, Generator
from .models import Answer, Chunk, Evidence
from .retrieval import SearchIndex


class KnowledgeAssistant:
    def __init__(self, chunks: list[Chunk], decision_provider: DecisionProvider | None = None,
                 generator: Generator | None = None) -> None:
        self.index = SearchIndex(chunks)
        self.decision_provider = decision_provider or LocalRules()
        self.generator = generator or ExtractiveGenerator()

    def ask(self, question: str, mode: str = "hybrid", top_k: int = 5) -> Answer:
        start = perf_counter()
        hits = self.index.search(question, mode=mode, top_k=top_k)
        candidates = tuple(Evidence(hit, self.decision_provider.judge(question, hit)) for hit in hits)
        eligible = [item.hit.chunk for item in candidates if item.decision.relevant and item.decision.covered]
        entities = named_entities(question)
        if len(entities) <= 1:
            accepted = eligible[:2] if "分别" in question else eligible[:1]
        else:
            accepted = []
            remaining = set(entities)
            for chunk in eligible:
                found = {entity for entity in remaining if entity in chunk.text.lower().replace(" ", "")}
                if found:
                    accepted.append(chunk)
                    remaining -= found
                if not remaining:
                    break
            if remaining:
                accepted = []
        text, usage = self.generator.generate(question, accepted)
        abstained = not accepted or text.strip() == "资料中没有足够证据，暂时无法确定。"
        return Answer(text, abstained, tuple(accepted) if not abstained else (), candidates,
                      mode, self.decision_provider.name, self.generator.name,
                      round((perf_counter() - start) * 1000, 3), usage)
