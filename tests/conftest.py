"""tests.conftest - 跨测试文件共用的桩

为什么抽出 stub_compliant_llm：编排类测试需要一份"一定合规"的 LLM 产出。
各写各的必然漂移——一份 stub 改了格式，另一份还按老格式造，
最后挂掉的是没人改过的那组测试。

它按 prompt 里给出的原文句子现造，不赌 LLM 语义质量：
真实模型的产物质量由 TestMechanicalGates 真测（那组不 stub）。
"""

from __future__ import annotations

import re


def stub_compliant_llm(prompt: str, **kw) -> dict:
    """按 prompt 里的句子造一份逐句覆盖的"合规图"。

    edges 留空——_clean_graph 会按原文明写顺序自动补成链。
    node_id / trace_to / writes / reads / measures 这些字段名跟抽取腿的
    产物契约一一对应，写错就会被 _clean_graph 判成幻觉剔光。
    """
    nodes, goal = [], ""
    tail = prompt.split("原文句子：")[-1]
    for line in tail.strip().split("\n"):
        m = re.match(r"(\S+)\s+\[(\w+)\]\s+(.*)", line.strip())
        if not m:
            continue
        sid, role, text = m.group(1), m.group(2), m.group(3)
        if role == "goal":
            goal = text
            continue
        nodes.append({
            "node_id": f"node_{len(nodes) + 1}", "action": text,
            "writes": [], "reads": [], "measures": [], "trace_to": sid,
        })
    return {"goal": goal, "nodes": nodes, "edges": []}