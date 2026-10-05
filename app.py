from __future__ import annotations

from pathlib import Path

import streamlit as st

from autoinsight.decisions import DecisionError, JevProvider, LocalRules
from autoinsight.evaluation import evaluate, load_cases
from autoinsight.generation import ExtractiveGenerator, GenerationError, OpenAIGenerator
from autoinsight.loaders import load_bytes, load_paths
from autoinsight.pipeline import KnowledgeAssistant

ROOT = Path(__file__).parent

st.set_page_config(page_title="AutoInsight Copilot", page_icon="🚙", layout="wide")
st.title("AutoInsight Copilot")
st.caption("汽车 VOC · FAQ · 竞品资料速查 ｜ 离线演示使用合成数据与本地规则")

with st.sidebar:
    st.header("资料与模式")
    suite = st.selectbox("本地资料集", ["demo", "expanded"],
                         format_func=lambda value: {"demo": "MVP 演示集", "expanded": "扩充评测集"}[value])
    uploads = st.file_uploader("导入资料（UTF-8 文本）", type=["md", "txt", "csv", "pdf", "docx"],
                               accept_multiple_files=True)
    retrieval = st.selectbox("检索", ["hybrid", "keyword"],
                             format_func=lambda value: {"hybrid": "混合检索", "keyword": "关键词检索"}[value])
    decision_mode = st.selectbox("判断", ["local", "jev"],
                                 format_func=lambda value: {"local": "本地规则演示", "jev": "真实 Jev"}[value])
    generation_mode = st.selectbox("回答", ["extractive", "openai"],
                                   format_func=lambda value: {"extractive": "原文摘录", "openai": "真实生成模型"}[value])
    model = st.text_input("OpenAI 模型名称", value="", disabled=generation_mode != "openai")
    st.info("真实接口从环境变量读取密钥。不会自动退回到演示模式。")

try:
    chunks = load_paths([ROOT / "data" / suite])
    for upload in uploads or []:
        chunks.extend(load_bytes(upload.name, upload.getvalue()))
except Exception as exc:
    st.error(f"资料导入失败：{exc}")
    st.stop()

st.caption(f"当前共 {len(chunks)} 个片段；扩充集混合公开事实的原创概述和虚构车型资料。")
default_question = ("星河X1标准版的演示续航是多少？" if suite == "demo"
                    else "云途A1 2025 款的演示续航是多少？")
question = st.text_input("输入问题", value=default_question, key=f"question_{suite}")

if st.button("查询", type="primary"):
    try:
        decision = JevProvider() if decision_mode == "jev" else LocalRules()
        generator = OpenAIGenerator(model) if generation_mode == "openai" else ExtractiveGenerator()
        answer = KnowledgeAssistant(chunks, decision, generator).ask(question, mode=retrieval)
    except (DecisionError, GenerationError, ValueError) as exc:
        st.error(str(exc))
    else:
        st.subheader("回答")
        if answer.abstained:
            st.warning(answer.text)
        else:
            st.write(answer.text)
        st.caption(f"判断：{answer.decision_provider} ｜ 回答：{answer.generation_provider} ｜ {answer.latency_ms} ms")
        if answer.usage:
            st.caption(f"实际调用用量：{answer.usage}")
        st.subheader("引用出处")
        if not answer.citations:
            st.write("无可引用证据")
        for index, chunk in enumerate(answer.citations, 1):
            st.markdown(f"**[{index}] {chunk.citation}**")
            st.write(chunk.text)
        with st.expander("查看检索与判断过程"):
            for item in answer.candidates:
                st.markdown(f"**{item.hit.chunk.citation}** · 检索 {item.hit.score:.2f} · "
                            f"相关 {item.decision.relevant} · 覆盖 {item.decision.covered}")
                st.caption(item.decision.reason)

st.divider()
st.subheader("离线评测")
st.caption("仅运行本地关键词检索、混合检索和规则判断；不代表 Jev 或生成模型表现。")
if st.button("运行所选资料集评测"):
    case_name = "questions.jsonl" if suite == "demo" else "expanded_questions.jsonl"
    result = evaluate(load_paths([ROOT / "data" / suite]),
                      load_cases(ROOT / "data" / "evaluation" / case_name), dataset=suite)
    st.dataframe([{"方案": name, **detail["metrics"]} for name, detail in result["variants"].items()],
                 hide_index=True, use_container_width=True)
    st.download_button("下载评测 JSON", __import__("json").dumps(result, ensure_ascii=False, indent=2),
                       file_name="evaluation.json", mime="application/json")
