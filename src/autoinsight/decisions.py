from __future__ import annotations

import os
import re
from typing import Protocol

from .models import Decision, Hit
from .retrieval import query_terms


class DecisionError(RuntimeError):
    pass


class DecisionProvider(Protocol):
    name: str

    def judge(self, question: str, hit: Hit) -> Decision: ...


class NoFilter:
    name = "none"

    def judge(self, question: str, hit: Hit) -> Decision:
        return Decision(True, True, hit.score, self.name, "仅按检索排序，未判断证据覆盖")


class LocalRules:
    name = "local_rules"

    def judge(self, question: str, hit: Hit) -> Decision:
        text = hit.chunk.text.lower()
        terms = query_terms(question)
        matched = {term for term in terms if term.lower() in text}
        term_ratio = len(matched) / max(len(terms), 1)
        entities = named_entities(question)
        years = set(re.findall(r"20\d{2}", question))
        normalized_text = text.replace(" ", "")
        correct_entity = not entities or any(entity in normalized_text for entity in entities)
        correct_entity = correct_entity and (not years or any(year in text for year in years))
        content_terms = {term for term in terms if term not in years and
                         not any(term.replace(" ", "") in entity for entity in entities)}
        content_terms -= {"云途", "星河", "竞品", "年款", "款", "资料", "记录", "具体", "内容", "给出"}
        semantic_aliases = {"座位": ("座椅", "六座", "座布局"), "试驾": ("试驾",), "预约": ("预约",),
                            "充电时间": ("charging", "minutes", "充电"), "多久": ("minutes", "分钟", "年", "个月")}
        searchable = f"{hit.chunk.location} {text}".lower()
        content_matched = {term for term in content_terms if term.lower() in searchable or
                           any(alias in searchable for alias in semantic_aliases.get(term, ())) }
        relevant = correct_entity and hit.score >= 0.12 and (term_ratio >= 0.25 or hit.vector_score >= 0.22)
        covered = relevant and term_ratio >= 0.5 and (not content_terms or bool(content_matched))
        if "取消" in searchable and "取消" not in question:
            covered = False
        reason = f"检索分 {hit.score:.2f}；关键项匹配 {len(matched)}/{len(terms)}"
        if not correct_entity:
            reason += "；对象不匹配"
        if content_terms and not content_matched:
            reason += "；所问信息未出现"
        return Decision(relevant, covered, round(term_ratio, 4), self.name, reason)


def named_entities(question: str) -> set[str]:
    lowered = question.lower()
    codes = re.findall(r"(?<![a-z])[a-z]\d+(?!\d)", lowered)
    competitors = re.findall(r"竞品\s*[ab](?![a-z0-9])", lowered)
    return {item.replace(" ", "") for item in [*codes, *competitors]}


class JevProvider:
    name = "jev_live"

    def __init__(self, api_key: str | None = None, threshold: float = 0.75) -> None:
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")
        if not self.api_key:
            raise DecisionError("真实 Jev 模式需要 TYPESAFE_API_KEY")
        self.threshold = threshold
        try:
            from typesafe_sdk import TypeSafeClient, Noul  # noqa: F401
        except ImportError as exc:
            raise DecisionError('请安装可选依赖：pip install -e ".[jev]"') from exc

    def judge(self, question: str, hit: Hit) -> Decision:
        from typesafe_sdk import Noul, TypeSafeClient

        try:
            with TypeSafeClient(api_key=self.api_key) as client:
                response = client.system_one(
                    state={"question": question, "source_excerpt": hit.chunk.text},
                    questions={
                        "relevant": Noul(instructions="Does the source excerpt discuss the specific subject and information requested by the question?"),
                        "covered": Noul(instructions="Does the source excerpt explicitly contain enough information to answer the question without guessing?"),
                    },
                )
            answers = getattr(response, "answers", None)
            if not isinstance(answers, dict):
                raise DecisionError("Jev 响应缺少 answers")
            relevant_p = float(getattr(answers["relevant"], "noul"))
            covered_p = float(getattr(answers["covered"], "noul"))
            if any(not 0 <= value <= 1 for value in (relevant_p, covered_p)):
                raise DecisionError("Jev 返回了无效概率")
        except DecisionError:
            raise
        except Exception as exc:
            raise DecisionError(f"Jev 调用失败：{exc}") from exc
        return Decision(relevant_p >= self.threshold, covered_p >= self.threshold,
                        round(covered_p, 4), self.name,
                        f"相关概率 {relevant_p:.2f}；覆盖概率 {covered_p:.2f}；阈值 {self.threshold:.2f}")
