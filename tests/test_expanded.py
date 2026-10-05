from collections import Counter
from pathlib import Path

import pytest

from autoinsight.evaluation import EvalCase, evaluate, load_cases
from autoinsight.loaders import load_paths
from autoinsight.models import Chunk
from autoinsight.stress import build_distractors, run_stress

ROOT = Path(__file__).parents[1]


def test_expanded_corpus_and_gold_locations():
    chunks = load_paths([ROOT / "data" / "expanded"])
    cases = load_cases(ROOT / "data" / "evaluation" / "expanded_questions.jsonl")
    assert len({chunk.source for chunk in chunks}) == 40
    assert len(cases) == 200
    assert {Path(chunk.source).suffix for chunk in chunks} == {".md", ".txt", ".csv", ".pdf", ".docx"}
    assert Counter(case.group for case in cases) == {
        "direct_faq": 40, "paraphrase": 30, "entity_version_confusion": 35,
        "cross_document": 25, "voc_competitor": 20, "missing_false_premise": 50,
    }
    assert all(any(chunk.source == source and chunk.location == location and required in chunk.text
                   for chunk in chunks) for case in cases if case.answerable
               for source, location, required in case.gold_evidence)


def test_equivalent_answer_and_exact_evidence():
    chunk = Chunk("one", "guide.md", "续航", "演示续航为 520 公里。")
    case = EvalCase("a", "演示续航是多少？", True, ("guide.md",), ("520 公里",), "direct",
                    (("520 公里", "五百二十公里"),), (("guide.md", "续航", "520 公里"),), "md")
    result = evaluate([chunk], [case], "test")
    row = result["variants"]["hybrid_local_rules"]["rows"][0]
    assert row["retrieval_recall_at_5"]
    assert row["retrieval_recall_at_10"]
    assert row["answer_correct"]
    assert row["citations_correct"]


def test_case_validation_rejects_empty_labels(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text('{"id":"x","question":"?","answerable":true,"group":"bad"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="可接受答案"):
        load_cases(path)


def test_stress_seed_and_sizes():
    base = [Chunk("anchor", "anchor.md", "正文", "唯一锚点：演示续航 520 公里。")]
    first = build_distractors(base, 30)
    second = build_distractors(base, 30)
    assert first == second
    assert len({chunk.id for chunk in first}) == 30
    result = run_stress(base, sizes=(30,))
    assert result["seed"] == 20261005
    assert result["rows"][0]["chunks"] == 30
