from __future__ import annotations

import os
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Protocol

from .models import Chunk


class GenerationError(RuntimeError):
    pass


class Generator(Protocol):
    name: str

    def generate(self, question: str, evidence: list[Chunk]) -> tuple[str, dict[str, int | float]]: ...


class ExtractiveGenerator:
    name = "extractive_local"

    def generate(self, question: str, evidence: list[Chunk]) -> tuple[str, dict[str, int | float]]:
        if not evidence:
            return "资料中没有足够证据，暂时无法确定。", {}
        lines = [f"{chunk.text} [{index}]" for index, chunk in enumerate(evidence, 1)]
        return "\n\n".join(lines), {}


class OpenAIGenerator:
    name = "openai_live"

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise GenerationError("真实生成模式需要 OPENAI_API_KEY")
        if not model.strip():
            raise GenerationError("真实生成模式需要明确填写模型名称")
        self.model = model.strip()

    def generate(self, question: str, evidence: list[Chunk]) -> tuple[str, dict[str, int | float]]:
        if not evidence:
            return "资料中没有足够证据，暂时无法确定。", {}
        source_text = "\n".join(f"[{index}] {chunk.citation}\n{chunk.text}" for index, chunk in enumerate(evidence, 1))
        try:
            payload = {
                "model": self.model,
                "store": False,
                "instructions": ("仅根据提供的资料片段回答中文问题。不得使用外部知识或猜测。"
                                 "每个事实后标注对应的 [编号]。若证据不足，只回答：资料中没有足够证据，暂时无法确定。"),
                "input": f"问题：{question}\n\n资料：\n{source_text}",
            }
            request = Request("https://api.openai.com/v1/responses",
                              data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              headers={"Authorization": f"Bearer {self.api_key}",
                                       "Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=30) as response:
                data = json.load(response)
            output = "\n".join(content.get("text", "")
                               for item in data.get("output", []) if item.get("type") == "message"
                               for content in item.get("content", []) if content.get("type") == "output_text").strip()
            if not output:
                raise GenerationError("生成模型没有返回文本")
            usage = data.get("usage")
            stats = {}
            if usage is not None:
                stats = {"input_tokens": int(usage.get("input_tokens", 0)),
                         "output_tokens": int(usage.get("output_tokens", 0))}
            return output, stats
        except GenerationError:
            raise
        except HTTPError as exc:
            raise GenerationError(f"生成模型调用失败：HTTP {exc.code}") from exc
        except URLError as exc:
            raise GenerationError(f"生成模型连接失败：{exc.reason}") from exc
        except Exception as exc:
            raise GenerationError(f"生成模型调用失败：{exc}") from exc
