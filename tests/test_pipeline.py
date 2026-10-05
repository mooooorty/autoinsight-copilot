from pathlib import Path
from io import BytesIO
import json
import sys
from types import SimpleNamespace

import pytest

from autoinsight.decisions import DecisionError, JevProvider, LocalRules
from autoinsight.evaluation import evaluate, load_cases
from autoinsight.generation import GenerationError, OpenAIGenerator
from autoinsight.loaders import load_paths
from autoinsight.pipeline import KnowledgeAssistant
from autoinsight.models import Hit

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def chunks():
    return load_paths([ROOT / "data" / "demo"])


def test_chinese_answer_with_exact_citation(chunks):
    answer = KnowledgeAssistant(chunks).ask("星河X1标准版的演示续航是多少？")
    assert not answer.abstained
    assert "520 公里" in answer.text
    assert answer.citations[0].citation == "星河X1_产品资料.md · 续航"
    assert answer.decision_provider == "local_rules"
    assert answer.generation_provider == "extractive_local"


@pytest.mark.parametrize("question", ["星河X3的续航是多少？", "星河X1的售价是多少？", "星河X2的电池容量是多少？"])
def test_missing_evidence_abstains(chunks, question):
    answer = KnowledgeAssistant(chunks).ask(question)
    assert answer.abstained
    assert answer.citations == ()


def test_cross_document_answer(chunks):
    answer = KnowledgeAssistant(chunks).ask("星河X1和星河X2的整车质保分别多久？")
    assert not answer.abstained
    assert {item.source for item in answer.citations} == {"星河X1_产品资料.md", "星河X2_产品资料.md"}
    assert "5 年或 12 万公里" in answer.text
    assert "6 年或 15 万公里" in answer.text


def test_optional_adapters_fail_clearly_without_keys(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(DecisionError, match="TYPESAFE_API_KEY"):
        JevProvider()
    with pytest.raises(GenerationError, match="OPENAI_API_KEY"):
        OpenAIGenerator("example-model")


def test_jev_sdk_adapter_imports_with_key(monkeypatch):
    monkeypatch.setitem(sys.modules, "typesafe_sdk", SimpleNamespace(TypeSafeClient=object, Noul=object))
    assert JevProvider(api_key="test-only-key").name == "jev_live"


def test_jev_decision_parses_sdk_response(monkeypatch, chunks):
    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "test-only-key"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def system_one(self, state, questions):
            assert "source_excerpt" in state
            assert set(questions) == {"relevant", "covered"}
            return SimpleNamespace(answers={"relevant": SimpleNamespace(noul=0.91),
                                            "covered": SimpleNamespace(noul=0.82)})

    monkeypatch.setitem(sys.modules, "typesafe_sdk", SimpleNamespace(TypeSafeClient=FakeClient,
                                                                       Noul=lambda **kwargs: kwargs))
    decision = JevProvider(api_key="test-only-key").judge("测试问题", Hit(chunks[0], 0.5, 0.5, 0.5))
    assert decision.relevant and decision.covered
    assert decision.provider == "jev_live"


def test_responses_request_uses_only_supplied_evidence(monkeypatch, chunks):
    seen = {}

    class FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["payload"] = json.loads(request.data)
        seen["timeout"] = timeout
        return FakeResponse(json.dumps({"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "演示续航为 520 公里。[1]"}]}],
            "usage": {"input_tokens": 50, "output_tokens": 12}}).encode())

    monkeypatch.setattr("autoinsight.generation.urlopen", fake_urlopen)
    generator = OpenAIGenerator("test-model", api_key="test-only-key")
    answer, usage = generator.generate("星河X1标准版续航？", [chunks[0]])
    assert answer == "演示续航为 520 公里。[1]"
    assert usage == {"input_tokens": 50, "output_tokens": 12}
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["payload"]["store"] is False
    assert seen["payload"]["model"] == "test-model"
    assert chunks[0].text in seen["payload"]["input"]


def test_evaluation_has_real_failure_checks(chunks):
    cases = load_cases(ROOT / "data" / "evaluation" / "questions.jsonl")
    assert len(cases) >= 40
    assert any(case.group == "cross_document" for case in cases)
    result = evaluate(chunks, cases)
    assert result["model_calls"] is False
    assert result["variants"]["hybrid_local_rules"]["metrics"]["abstention_accuracy_missing"] >= 0.7
    assert result["variants"]["hybrid"]["metrics"]["abstention_accuracy_missing"] < 0.7
