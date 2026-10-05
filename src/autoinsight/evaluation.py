from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

from .decisions import LocalRules, NoFilter
from .models import Chunk
from .pipeline import KnowledgeAssistant


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    answerable: bool
    expected_sources: tuple[str, ...]
    expected_phrases: tuple[str, ...]
    group: str
    acceptable_answers: tuple[tuple[str, ...], ...] = ()
    gold_evidence: tuple[tuple[str, str, str], ...] = ()
    document_format: str = "legacy"


def load_cases(path: Path) -> list[EvalCase]:
    cases = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            answers = tuple(tuple(group) for group in row.get("acceptable_answers", []))
            evidence = tuple((item["source"], item["location"], item["required_text"])
                             for item in row.get("gold_evidence", []))
            case = EvalCase(row["id"], row["question"], row["answerable"],
                            tuple(row.get("expected_sources", [])), tuple(row.get("expected_phrases", [])),
                            row["group"], answers, evidence, row.get("document_format", "legacy"))
            if not case.id or not case.question.strip() or not isinstance(case.answerable, bool):
                raise ValueError(f"第 {number} 行的标注字段无效")
            if case.answerable and not (case.acceptable_answers or case.expected_phrases):
                raise ValueError(f"第 {number} 行缺少可接受答案")
            if case.answerable and not (case.gold_evidence or case.expected_sources):
                raise ValueError(f"第 {number} 行缺少证据来源")
            if any(not group or any(not answer for answer in group) for group in answers):
                raise ValueError(f"第 {number} 行可接受答案为空")
            cases.append(case)
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("评测问题 ID 重复")
    return cases


def evaluate(chunks: list[Chunk], cases: list[EvalCase], dataset: str = "synthetic_demo") -> dict:
    if not cases:
        raise ValueError("评测集为空")
    configurations = [
        ("keyword", "keyword", NoFilter()),
        ("hybrid", "hybrid", NoFilter()),
        ("hybrid_local_rules", "hybrid", LocalRules()),
    ]
    variants = {}
    for name, search_mode, provider in configurations:
        assistant = KnowledgeAssistant(chunks, decision_provider=provider)
        rows = []
        for case in cases:
            result = assistant.ask(case.question, mode=search_mode)
            top_ten = assistant.index.search(case.question, mode=search_mode, top_k=10)
            retrieved = {item.hit.chunk.source for item in result.candidates}
            cited = {chunk.source for chunk in result.citations}
            expected = set(case.expected_sources)
            retrieved_chunks = [item.hit.chunk for item in result.candidates]
            if case.gold_evidence:
                def matches(pool):
                    return all(any(chunk.source == source and chunk.location == location and
                                   required in chunk.text for chunk in pool)
                               for source, location, required in case.gold_evidence)
                retrieval_ok = matches(retrieved_chunks) if case.answerable else None
                retrieval_ten = matches([hit.chunk for hit in top_ten]) if case.answerable else None
                citations_ok = (not result.abstained and matches(result.citations) and
                                all(any(chunk.source == source and chunk.location == location
                                        for source, location, _ in case.gold_evidence)
                                    for chunk in result.citations)) if case.answerable else result.abstained
            else:
                def matches(pool):
                    return all(any(phrase in chunk.text and chunk.source in expected for chunk in pool)
                               for phrase in case.expected_phrases)
                retrieval_ok = matches(retrieved_chunks) if case.answerable else None
                retrieval_ten = matches([hit.chunk for hit in top_ten]) if case.answerable else None
                citations_ok = (not result.abstained and cited.issubset(expected) and
                                all(any(phrase in chunk.text for chunk in result.citations)
                                    for phrase in case.expected_phrases)) if case.answerable else result.abstained
            answer_groups = case.acceptable_answers or tuple((phrase,) for phrase in case.expected_phrases)
            answer_ok = (not result.abstained and all(any(option in result.text for option in group)
                                                      for group in answer_groups)) if case.answerable else result.abstained
            rows.append({
                "id": case.id, "group": case.group, "document_format": case.document_format,
                "question": case.question,
                "answerable": case.answerable, "retrieval_recall_at_5": retrieval_ok,
                "retrieval_recall_at_10": retrieval_ten,
                "answer_correct": answer_ok, "citations_correct": citations_ok,
                "abstained": result.abstained, "cited_sources": sorted(cited),
                "retrieved_sources": sorted(retrieved), "latency_ms": result.latency_ms,
            })
        answerable = [row for row in rows if row["answerable"]]
        missing = [row for row in rows if not row["answerable"]]
        variants[name] = {
            "metrics": {
                "cases": len(rows),
                "retrieval_recall_at_5": _rate(row["retrieval_recall_at_5"] for row in answerable),
                "retrieval_recall_at_10": _rate(row["retrieval_recall_at_10"] for row in answerable),
                "answer_accuracy": _rate(row["answer_correct"] for row in rows),
                "citation_accuracy_answerable": _rate(row["citations_correct"] for row in answerable),
                "abstention_accuracy_missing": _rate(row["abstained"] for row in missing),
                "false_abstention_rate_answerable": _rate(row["abstained"] for row in answerable),
                "mean_latency_ms": round(mean(row["latency_ms"] for row in rows), 3),
                "p50_latency_ms": round(median(row["latency_ms"] for row in rows), 3),
                "p95_latency_ms": _percentile([row["latency_ms"] for row in rows], .95),
                "api_cost_usd": None,
            },
            "by_group": _breakdown(rows, "group"),
            "by_format": _breakdown(rows, "document_format"),
            "rows": rows,
        }
    return {"dataset": dataset, "model_calls": False, "scoring": "automatic_string_and_exact_evidence",
            "human_review": "not_performed_by_evaluator", "variants": variants}


def write_report(result: dict, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "evaluation.json"
    md_path = output / "evaluation.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# 离线评测：{result['dataset']}", "", "自动字符串及精确证据判分；未调用 Jev 或生成模型。人工抽查需另行记录。", "",
             "| 方案 | 召回@5 | 召回@10 | 回答准确率 | 出处正确率 | 缺失题拒答率 | 误拒答率 | p50/p95 延迟(ms) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, detail in result["variants"].items():
        metrics = detail["metrics"]
        lines.append(f"| {name} | {metrics['retrieval_recall_at_5']:.1%} | {metrics['retrieval_recall_at_10']:.1%} | "
                     f"{metrics['answer_accuracy']:.1%} | {metrics['citation_accuracy_answerable']:.1%} | "
                     f"{metrics['abstention_accuracy_missing']:.1%} | {metrics['false_abstention_rate_answerable']:.1%} | "
                     f"{metrics['p50_latency_ms']:.1f}/{metrics['p95_latency_ms']:.1f} |")
        for label, key in (("题型", "by_group"), ("格式", "by_format")):
            lines.extend(["", f"## {name} · {label}", "", "| 类别 | 题数 | 回答正确率 | 出处正确率 |", "|---|---:|---:|---:|"])
            for category, score in detail[key].items():
                lines.append(f"| {category} | {score['cases']} | {score['answer_accuracy']:.1%} | {score['citation_accuracy']:.1%} |")
    lines += ["", "## 失败样例", ""]
    for name, detail in result["variants"].items():
        failures = [row for row in detail["rows"] if not row["answer_correct"] or not row["citations_correct"]]
        lines.append(f"### {name} ({len(failures)} 条)")
        lines += [f"- {row['id']} {row['question']}（回答={row['answer_correct']}，出处={row['citations_correct']}）"
                  for row in failures[:12]]
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def _rate(values) -> float:
    items = list(values)
    return round(sum(bool(value) for value in items) / len(items), 4) if items else 0.0


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction + .5))
    return round(ordered[index], 3)


def _breakdown(rows: list[dict], field: str) -> dict:
    return {key: {"cases": len(subset),
                  "answer_accuracy": _rate(row["answer_correct"] for row in subset),
                  "citation_accuracy": _rate(row["citations_correct"] for row in subset)}
            for key in sorted({row[field] for row in rows})
            for subset in [[row for row in rows if row[field] == key]]}
