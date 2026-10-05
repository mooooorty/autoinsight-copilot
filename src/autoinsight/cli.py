from __future__ import annotations

import argparse
import json
from pathlib import Path

from .decisions import JevProvider, LocalRules, NoFilter
from .evaluation import evaluate, load_cases, write_report
from .generation import ExtractiveGenerator, OpenAIGenerator
from .loaders import load_paths
from .pipeline import KnowledgeAssistant


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoInsight 汽车资料智能速查")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="查询资料")
    ask.add_argument("question")
    ask.add_argument("--suite", choices=["demo", "expanded"], default="demo")
    ask.add_argument("--data", type=Path)
    ask.add_argument("--retrieval", choices=["keyword", "hybrid"], default="hybrid")
    ask.add_argument("--decision", choices=["none", "local", "jev"], default="local")
    ask.add_argument("--generation", choices=["extractive", "openai"], default="extractive")
    ask.add_argument("--model", default="")
    evaluation = commands.add_parser("evaluate", help="运行合成问题评测")
    evaluation.add_argument("--suite", choices=["demo", "expanded"], default="demo")
    evaluation.add_argument("--data", type=Path)
    evaluation.add_argument("--cases", type=Path)
    evaluation.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    data_path = args.data or Path("data") / args.suite
    chunks = load_paths([data_path])
    if not chunks:
        parser.error("资料目录没有可用文本")
    if args.command == "evaluate":
        case_path = args.cases or Path("data/evaluation") / ("questions.jsonl" if args.suite == "demo" else "expanded_questions.jsonl")
        output_path = args.output or Path("reports/generated") / args.suite
        result = evaluate(chunks, load_cases(case_path), dataset=args.suite)
        json_path, md_path = write_report(result, output_path)
        print(json.dumps({name: detail["metrics"] for name, detail in result["variants"].items()},
                         ensure_ascii=False, indent=2))
        print(f"报告：{json_path}；{md_path}")
        return 0
    decision = {"none": NoFilter, "local": LocalRules, "jev": JevProvider}[args.decision]()
    generator = OpenAIGenerator(args.model) if args.generation == "openai" else ExtractiveGenerator()
    result = KnowledgeAssistant(chunks, decision, generator).ask(args.question, mode=args.retrieval)
    print(result.text)
    for index, chunk in enumerate(result.citations, 1):
        print(f"[{index}] {chunk.citation}")
    print(f"来源模式：{result.decision_provider} / {result.generation_provider}；耗时 {result.latency_ms} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
