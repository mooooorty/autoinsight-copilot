"""Deterministic in-memory retrieval scale check; no model or external API calls."""
from __future__ import annotations

import argparse
import json
import random
import tracemalloc
from pathlib import Path
from statistics import median
from time import perf_counter

from .loaders import load_paths
from .models import Chunk
from .retrieval import SearchIndex, tokens

SEED = 20261005


def build_distractors(base: list[Chunk], target: int, seed: int = SEED) -> list[Chunk]:
    if not base or target < len(base):
        raise ValueError("target 必须不小于基础片段数")
    rng = random.Random(seed)
    topics = ["电池温控", "座舱照明", "轮胎保养", "停车辅助", "地图更新", "后备厢清洁", "空调滤芯"]
    result = list(base)
    for number in range(target - len(base)):
        topic = topics[rng.randrange(len(topics))]
        overlap = base[0].text[:6] if number % 10 == 0 else ""
        result.append(Chunk(f"noise-{number:05d}", f"干扰资料_{number:05d}.txt", "正文",
                            f"虚构资料编号 {number:05d}：{overlap}{topic}的记录批次为 {rng.randrange(10000, 99999)}。"))
    return result


def run_stress(base: list[Chunk], sizes: tuple[int, ...] = (500, 2000, 5000), seed: int = SEED) -> dict:
    if not base:
        raise ValueError("基础资料为空")
    anchor = base[0]
    questions = [anchor.text[: min(24, len(anchor.text))]] * 30
    tokens("索引热身")
    rows = []
    for size in sizes:
        corpus = build_distractors(base, size, seed)
        tracemalloc.start()
        start = perf_counter()
        index = SearchIndex(corpus)
        build_ms = (perf_counter() - start) * 1000
        timings = []
        recall = 0
        for question in questions:
            start = perf_counter()
            hits = index.search(question, top_k=5)
            timings.append((perf_counter() - start) * 1000)
            recall += any(hit.chunk.id == anchor.id for hit in hits)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rows.append({"chunks": size, "build_ms": round(build_ms, 2),
                     "query_p50_ms": round(median(timings), 2),
                     "query_p95_ms": round(sorted(timings)[int((len(timings) - 1) * .95 + .5)], 2),
                     "anchor_recall_at_5": round(recall / len(questions), 4),
                     "python_traced_peak_mib": round(peak / 1024**2, 2)})
    return {"seed": seed, "queries_per_size": len(questions), "memory_measure": "Python traced allocations only",
            "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoInsight 本地检索压力测试")
    parser.add_argument("--data", type=Path, default=Path("data/expanded"))
    parser.add_argument("--output", type=Path, default=Path("reports/generated/stress.json"))
    args = parser.parse_args()
    result = run_stress(load_paths([args.data]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
