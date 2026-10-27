"""tests.conftest - 跨测试文件共用的桩

为什么抽出 stub_compliant_llm：编排类测试需要一份"一定合规"的 LLM 产出。
各写各的必然漂移——一份 stub 改了格式，另一份还按老格式造，
最后挂掉的是没人改过的那组测试。

它按 prompt 里给出的原文句子现造，不赌 LLM 语义质量：
真实模型的产物质量由 TestMechanicalGates 真测（那组不 stub）。
"""

from __future__ import annotations

import re

import pytest


def stub_compliant_llm(prompt: str, **kw) -> dict:
    """按 prompt 里的句子造一份逐句覆盖的"合规图"。

    edges 留空——_clean_graph 会按原文明写顺序自动补成链。
    node_id / trace_to / writes / reads / measures 这些字段名跟抽取腿的
    产物契约一一对应，写错就会被 _clean_graph 判成幻觉剔光。

    **每条内容句（[goal] / [fact]）都要有节点指向它**——
    覆盖率的分母就是这批句子，漏一句就判红。[title] 是名字，不抽节点。

    **roles 也要给**：句子的角色由模型判（机械层只守下限），
    缺了 roles 的输出会被抽取腿判成「模型没按要求履约」→ 中止。
    这里原样回抄 prompt 里的机械初判——测试关心的是机械层的行为，
    角色的语义判断由 tests/test_wms_requirement_extraction.py 单独测。
    """
    nodes, goal, title = [], "", ""
    roles: dict[str, str] = {}
    tail = prompt.split("原文句子：")[-1]
    for line in tail.strip().split("\n"):
        m = re.match(r"(\S+)\s+\[(\w+)\]\s+(.*)", line.strip())
        if not m:
            continue
        sid, role, text = m.group(1), m.group(2), m.group(3)
        roles[sid] = role
        if role == "goal":
            goal = text
        elif role == "title":
            # [title] 是「这次要建模的东西的名字」，不是需求，不抽节点
            title = text
            continue
        nodes.append({
            "node_id": f"node_{len(nodes) + 1}", "action": text,
            "guard": "", "writes": [], "reads": [], "measures": [],
            "trace_to": sid,
        })
    return {"roles": roles, "goal": goal or title, "nodes": nodes, "edges": []}


def auto_roles(prompt: str) -> dict[str, str]:
    """从 prompt 的「原文句子：」段落里取出机械初判的角色

    测试桩默认不判角色（它只关心机械层的行为），所以这里原样回抄。
    **要测角色的语义判断，就在桩里显式给 roles。**
    """
    roles: dict[str, str] = {}
    tail = prompt.split("原文句子：")[-1]
    for line in tail.strip().split("\n"):
        m = re.match(r"(\S+)\s+\[(\w+)\]\s+(.*)", line.strip())
        if m:
            roles[m.group(1)] = m.group(2)
    return roles


# ============================================================
# WMS 概念绑定的桩
# ============================================================

def _match_keyword(text: str, keywords):
    """在**原文**里找第一个命中的关键词，返回 (概念用的词, 原文里的那一段)

    返回原文里的那一段（不是词表里的大小写形式）——
    因为机械层要求 evidence **逐字**出现在需求描述里。
    词表里写 "sku"、原文写 "SKU" 时，回 "sku" 会被判成「证据不是原文」。
    """
    low = text.lower()
    for kw in keywords:
        idx = low.find(kw.lower())
        if idx >= 0:
            return kw, text[idx:idx + len(kw)]
    return None, None


def stub_wms_binder(prompt: str, **kw) -> dict:
    """按词表造一份 WMS 绑定结果（**测试桩，不是产品逻辑**）

    它复刻的是**改造前**的关键词绑定行为，所以既有测试的期望值不用改。
    真实路径走 LLM（boxes/wms_design/binder.py），语义质量由那组测试真测。

    桩也守机械层的要求：evidence 必须是原文里真实出现的那一段。
    """
    from boxes.wms_design.domain import (
        WMS_CONSTRAINTS,
        WMS_ENTITIES,
        WMS_METRIC_KEYWORDS,
        WMS_REPORTS,
        requirement_sentences,
    )

    tail = prompt.split("需求列表：")[-1]
    bindings = []
    for line in tail.strip().split("\n"):
        line = line.strip()
        if not line.startswith("- id="):
            continue
        m = re.match(r"- id=(\S+)\s+type=([^\s（(]+)", line)
        if not m:
            continue
        rid, rtype = m.group(1), m.group(2)
        # 描述在「描述：」之后（类型后面可能带位置提示，别把它吃进描述里）
        desc_m = re.search(r"描述：(.*)$", line)
        description = desc_m.group(1) if desc_m else ""

        kinds = []
        # 报表概念**不看类型**——「临期 SKU 要能在库存看板看到」这句话
        # 放在需求盒的哪个位置上都不影响它讲的是一张报表。
        # （真实路径里类型只是提示，桩也得跟上，否则桩比产品还严。）
        for kind, spec in WMS_REPORTS.items():
            _, evidence = _match_keyword(description, spec["keywords"])
            if evidence:
                kinds.append({"kind": kind, "evidence": evidence})
                break
        if rtype in ("object", "process", "event"):
            for kind, spec in WMS_ENTITIES.items():
                _, evidence = _match_keyword(description, spec["keywords"])
                if evidence:
                    kinds.append({"kind": kind, "evidence": evidence})
                    break
        elif rtype == "rule":
            for sentence in requirement_sentences(description):
                for kind, spec in WMS_CONSTRAINTS.items():
                    _, evidence = _match_keyword(sentence, spec["keywords"])
                    if evidence:
                        if not any(k["kind"] == kind for k in kinds):
                            kinds.append({"kind": kind, "evidence": evidence})
                        break
        elif rtype == "metric":
            for name, keywords in WMS_METRIC_KEYWORDS.items():
                _, evidence = _match_keyword(description, keywords)
                if evidence:
                    kinds.append({"kind": name, "evidence": evidence})
                    break
        bindings.append({"requirement_id": rid, "kinds": kinds})
    return {"bindings": bindings}


# ============================================================
# 分流桩：仓里有几条 LLM 腿，就集中在这里认全
# ============================================================

def stub_llm_dispatch(prompt: str, **kw) -> dict:
    """按 prompt 里的标记分流到对应的桩

    仓里有两条 LLM 腿：
    - 需求抽取（boxes/wms_requirement）—— prompt 里有「原文句子：」
    - WMS 概念绑定（boxes/wms_design/binder）—— prompt 里有「需求列表：」

    各写各的桩必然漂移：一条 prompt 改了格式，另一条还按老格式造，
    最后挂掉的是没人改过的那组测试。
    """
    if "需求列表：" in prompt:
        return stub_wms_binder(prompt, **kw)
    if "原文句子：" in prompt:
        return stub_compliant_llm(prompt, **kw)
    from runtime import llm

    raise llm.LLMError("test: no stub for this prompt")


@pytest.fixture
def stub_llm(monkeypatch):
    """装上分流桩（**显式**申请，不做 autouse）

    为什么不做 autouse：autouse 的执行顺序在 conftest 与模块之间并不可靠，
    而本仓有两组测试对 LLM 的要求正好相反——
    编排测试要桩，客户端重试测试要**真的** complete_json。
    靠顺序决定谁生效，迟早会静默错掉一边。
    """
    from runtime import llm

    monkeypatch.setattr(llm, "complete_json", stub_llm_dispatch)
    return stub_llm_dispatch