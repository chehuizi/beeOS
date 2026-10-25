"""
boxes.requirement_capture.runner - 需求捕获盒的 5 个 operation 实现

从 runtime/capture_runner.py 搬来。盒子以前只带 definition / schemas / measure，
执行部分躺在内核里——它带不走自己的实现，也就不算一个自包含单元。

**这条腿是真的**：extract_requirements 真调 LLM（自然语言理解是真的），
其余 4 个 op 是纯机械的（切句 / 编号 / 集合运算 / 字符串匹配）。
所以它是「LLM 只做语义、机械做校验」这条边界的落地：
真上生产时只有 extract_requirements 需要换成真实模型调用，
其余 4 个算子可以永远保持确定性。

**它不含降级**：LLM 不可用就是履约中止，没有第二条腿兜着。
规则版抽取是唯一稳定拿绿灯的腿，可它绕过了质量门禁——
降级不是兜底，是绕过检查的通道。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any
from uuid import uuid4

from runtime.runner import HandlerRunner


_TYPE_PREFIX = {
    "object": "obj",
    "rule": "rule",
    "process": "proc",
    "metric": "met",
    "event": "evt",
    "goal": "goal",
}

# 句末标点（不含顿号——顿号是句内枚举，不切句）
_SENT_SPLIT = re.compile(r"[。；;！!？?\n]+")
# 句内并列分隔符：**只有顿号**。
#
# 以前这里还包含中文逗号，于是
#   「仓库出库的时候，同一个 SKU 必须先出快过期的批次」
# 被切成「仓库出库的时候」+「同一个 SKU 必须先出快过期的批次」两半——
# 前半句是状语从句（**铺垫**），后半句才是需求。
#
# 铺垫句进了覆盖率的分母 → 真需求只覆盖了一半 → 需求盒判红
# （实测 source_coverage 50%，uncovered_sentences=["sent_1"]）。
# **中文逗号连着的是同一个句子**，不是枚举。
_ENUM_SEP = re.compile(r"、")
# 存在性声明词：左侧是目标，右侧每项是业务事实
_EXIST_WORDS = ("有", "包含", "包括", "分为", "涵盖", "涉及")
_ENUM_MIN_LEN = 2

# ---- 「名字」的样子（机械判 title 时的护栏）----
#
# 「存在性词左边只剩一个名字」——这里的「名字」是有要求的。
# 「出库必须包含 A、B」里，「出库必须」不是名字，是半句要求：
# 把它当标题丢掉，这条要求就整个不见了（**假绿**）。
_NAME_MAX_LEN = 12
_NAME_FORBIDDEN = (
    # 要求 / 情态
    "必须", "应当", "应该", "不得", "不能", "禁止", "要求", "保证", "确保",
    "优先", "至少", "最多", "超过", "低于", "大于", "小于", "支持", "需要",
    "要", "需",
    # 条件 / 关联（这些开头的句子是从句，不是名字）
    "如果", "假如", "假设", "一旦", "由于", "因为", "为了", "所以", "但是",
    "不过", "而且", "并且", "然后", "其中", "关于", "以及", "或者",
)

# ---- 要求表达（机械下限）----
#
# 句子里出现这些词，说明**它本身带着一条要求**，不许被排除在覆盖率分母之外。
#
# 这张表是下限不是全集。漏掉一种说法，最坏是那句要求没进分母（需求被漏）；
# 收得太宽，铺垫句会被拉进分母（假红）。所以不收「要」「需要」这种裸词——
# 「需要注意的是」不是要求，「要支持」才是。
_REQUIRE_MARKERS = (
    "必须", "应当", "应该", "不得", "不能", "禁止", "要求",
    "至少", "最多", "不超过", "不低于", "保证", "确保",
    "要能", "要可", "要支持", "要看到", "应能", "应支持", "应把",
    "才能", "需要满足", "需要支持", "需要能", "需在", "需支持", "须在",
)


# ============================================================
# op 1: split_source_sentences —— 机械切句
# ============================================================


# 句子的四种角色
#
# **角色是句子的性质，不是它在原文里的位置。**
#
# 以前只有 goal / fact 两种，规则是「首句 = goal，其余 = fact」。
# 那条规则有个致命后果：**业务方真正的需求通常就在首句里**。
#
#   「同一 SKU 出库必须先出快过期的批次；临期 30 天内 SKU 要能在库存看板看到」
#    └ 首句 = goal ─────────────────────────────┘  └ fact ──────────────┘
#
# 首句是 goal → 不抽节点 → **「必须先出快过期的批次」这条需求整个丢了**，
# 只剩一句 business_goal 文本躺在那儿。设计盒的绑定腿只读 requirements，
# 于是这个 WMS 概念从头到尾没进过链——测试盒的探针也就无从谈起。
#
# 四种角色：
#   title   —— 这次要建模的东西的**名字**（"库存管理"）。只是名字，不是需求。
#   goal    —— 业务目标句，**它本身就是那句要满足的要求**，要抽节点。
#   fact    —— 业务事实句，抽节点。
#   context —— **铺垫句**：场景 / 时间状语 / 语气说明，**本身不含任何独立的要求**。
#
# **谁判**：
#   - `title` 由**机械层**判（存在性枚举 + 左边像个名字）。
#   - `goal / fact / context` 由 **LLM** 判——「这句里有没有独立的要求」是语义问题。
#     机械层按位置判过一次，代价是**铺垫句进了覆盖率的分母**：
#     「仓库出库的时候」这种状语从句被当成一句需求，
#     而它抽不出任何东西 → source_coverage 永远 < 100 → **对真需求假红**。
#   - 机械层在 LLM 之上守**下限**：含要求表达的句**必须**进分母，
#     不许被说成「只是铺垫」。
#
# title 只在一种情况下出现：首句是「X 有/包含/分为 A、B、C」这种
# 存在性枚举，且存在性词左边确实只剩一个**像名字**的名字。
# 找不到存在性词时（「库存管理要支持入库、出库」），首句**仍是内容句**——
# 「入库」就藏在它里面，当标题丢掉是丢信息。
_ROLES = ("title", "goal", "fact", "context")
_CONTENT_ROLES = ("goal", "fact")


def _looks_like_name(text: str) -> bool:
    """这段文字像个「名字」吗（机械判 title 时的护栏）

    「存在性词左边只剩一个名字」——「出库必须」不是名字。
    当成名字丢掉，这条要求就整个不见了。
    """
    t = (text or "").strip()
    if not t or len(t) > _NAME_MAX_LEN:
        return False
    return not any(w in t for w in _NAME_FORBIDDEN)


def _has_requirement_marker(text: str) -> bool:
    """这句里有没有「要求」的意思（机械下限）

    命中 = 这句自带一条要求，不许被排除在覆盖率分母之外。
    """
    return any(w in (text or "") for w in _REQUIRE_MARKERS)


def _content_sentences(sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """可抽内容的句子：goal + fact（title 只是名字，context 只是铺垫）

    覆盖率的分母 = 这批句子的数量。
    title 不进分母——「库存管理」这种名字抽不出需求，让它进分母就是逼模型编碎片。
    context 也不进分母——「仓库出库的时候」这种铺垫句同理。
    """
    return [
        s for s in sentences
        if s.get("role") in _CONTENT_ROLES and (s.get("text") or "").strip()
    ]


def _goal_text(sentences: list[dict[str, Any]]) -> str:
    """业务目标：优先 [goal] 句，没有就取 [title] 句（它就是这次要建模的东西的名字）"""
    for role in ("goal", "title"):
        for s in sentences:
            if s.get("role") == role and (s.get("text") or "").strip():
                return s["text"]
    return ""


def _split_sentences(narrative: str) -> list[dict[str, Any]]:
    """自然语言 → 带 id 的句子列表（role: title / goal / fact）

    这里只做**机械能确定**的判断：
    - 按句末标点切句
    - 首句是存在性枚举（「X 有 A、B、C」）且 X 像个名字 → X = title，各项 = fact
    - 其余首句 = goal，其余句 = fact（**这是初判，不是结论**）

    **角色是句子的性质，不是它的位置**——所以 goal / fact / context 的最终判定
    交给 LLM（见 `_extract_roles`），机械层只保留 title 的判定权，
    并在 LLM 之上守下限（含要求表达的句必须进分母）。
    """
    raw = [s.strip() for s in _SENT_SPLIT.split(narrative) if s.strip()]
    if not raw:
        return []

    sentences: list[dict[str, Any]] = []

    def _add(text: str, role: str) -> None:
        if text:
            sentences.append({
                "sentence_id": f"sent_{len(sentences) + 1}",
                "text": text,
                "role": role,
            })

    head, rest = raw[0], raw[1:]

    # 首句内含顿号枚举时，名字取存在性词左侧，枚举项各自成句
    parts = [p.strip() for p in _ENUM_SEP.split(head) if p.strip()]
    parts = [p for p in parts if len(p) >= _ENUM_MIN_LEN] or parts[:1]
    if len(parts) >= 2:
        first, enum_items = parts[0], parts[1:]
        head_text = first
        is_title = False
        for w in _EXIST_WORDS:
            if w not in first:
                continue
            candidate = first.split(w)[0].strip() or first
            # **左边不像名字 → 这不是「X 有 A、B」这种枚举**
            # （「出库必须包含 A、B」的「出库必须」不是名字）
            if not _looks_like_name(candidate):
                continue
            lead = first.split(w, 1)[1].strip()
            head_text = candidate
            is_title = True
            if lead:
                enum_items = [lead, *enum_items]
            break
        # 存在性词左边确实只剩一个名字 → title（不抽节点）
        # 找不到存在性词 → 首句仍可能是内容（「库存管理要支持入库」里就有「入库」）
        _add(head_text, "title" if is_title else "goal")
        for item in enum_items:
            _add(item, "fact")
    else:
        _add(head, "goal")

    for s in rest:
        _add(s, "fact")
    return sentences


def _simulate_source_sentence_splitter(input_data: dict[str, Any]) -> dict[str, Any]:
    narrative = input_data.get("narrative", "")
    return {
        "set_id": input_data.get("set_id") or f"req_set_nl_{uuid4().hex[:8]}",
        "business_goal": "",
        "source_sentences": _split_sentences(narrative),
        "nodes": [],
        "edges": [],
        "requirements": [],
        "requirement_count": 0,
        "narrative": narrative,
    }


# ============================================================
# op 2: extract_requirements —— 唯一走 LLM 的 op
# ============================================================

_EXTRACT_PROMPT = """从下面这份中文业务表述里抽出一张**流程图**。只输出 JSON，不要解释、不要分析、不要复述原文。

业务方描述一件事，说的是流程图：节点（做什么）+ 边（走到哪）+ 守卫（什么条件下才走）。
不要把它摊平成一串需求——摊平会把"哪个约束挂在哪个动作上"这个信息弄丢。

原文已按句子切分，每句有 sentence_id 和**机械初判**的角色：
- [title] 是这次要建模的东西的**名字**（例如"库存管理"）。它只是标题，**不要**为它抽节点。
- [goal] 是业务目标。**它本身就是这次要满足的要求**——必须至少抽一个节点指向它。
- [fact] 是业务事实。每个动作 / 约束各自成节点。

**上面那个角色只是初判，你要自己重新判一遍**，并在输出里给出每句话的角色（见下面的 roles 字段）：
- "goal"：这句是业务目标，或者它本身就是一条要满足的要求
- "fact"：这句是业务事实（实体、动作、约束、指标）
- "context"：**这句只是铺垫**——场景、时间状语、语气说明、背景交代，
  它**本身不含任何独立的要求**。例：「仓库出库的时候」「简单来说」「目前的情况是」
- "title"：**这句只是这次要建模的东西的名字**，没有任何要求。例：「库存管理」

⚠️ 只有真的**没有任何要求**的句子才能标 context 或 title。
⚠️ 带着要求的句子（哪怕写成陈述句）都要标 goal 或 fact。
   标错的代价很具体：标成 context 的句子**不算进覆盖率**，
   那条要求就没人认领——而没人会发现。

你要做的：
1. 只用原文中真实存在的内容，绝对不要补充、推断或编造
2. nodes：每个业务动作一个节点。action 写原文里出现的片段，不要改写
3. **每一条 [goal] / [fact] 句子都至少要有一个节点 trace_to 指向它**——
   漏掉一句，那句原文就没人认领，覆盖率判据会红
   （你在 roles 里标成 context / title 的句子除外——那些本来就没有要求）
4. edges：把节点按原文的先后顺序串起来，from/to 用 node_id
5. 同一句里如果既有动作又有约束，动作进 nodes，约束写进**那一步之后那条边的 guard**；
   **整张图只有一个节点的时候没有边可挂**，约束就写进这个节点自己的 guard 字段
6. 动作产出或读入的实体写进 writes / reads；说要量的指标写进 measures
7. 每个节点和边都填 trace_to，指向它依据的那一句的 sentence_id
8. goal 字段写 [goal] 句的原文；没有 [goal] 句就写 [title] 句的原文

输出格式：
{{"roles": {{"sent_1": "goal", "sent_2": "context"}},
 "goal": "首句原文片段",
 "nodes": [{{"action": "原文片段", "guard": "", "writes": [], "reads": [], "measures": [],
              "trace_to": "sent_2"}}],
 "edges": [{{"from": "node_1", "to": "node_2", "guard": "", "trace_to": "sent_3"}}]}}

注意：
- roles 要**每句话都有**，一个都不能漏
- 没有守卫时 guard 留空字符串，不要编一个
- 没有产出实体时 writes / reads 留空数组
- measures 只在原文明确说要度量什么时才填

直接输出这段 JSON 就好，不要在 JSON 前后加任何文字。
原文句子：
{sentences}"""


def _attach_sources(
    fragments: list[str],
    sentences: list[dict[str, Any]],
    content_ids: set[str] | None = None,
) -> list[tuple[str, str]]:
    """把挂在节点身上的元素，逐个归到它真实出现的那一句上

    覆盖率按「每句都有图上元素指向它」算。节点 action 之外的实体 / 指标
    可能来自别的句子，不归位就会被误判成"这句没被覆盖"。
    归不到任何一句 → 是编的，返回空丢掉。

    **只认内容句（goal / fact）**：实体 / 指标写在标题句里，
    那还是编的——标题是名字，"库存管理"里读不出一个实体。
    """
    out: list[tuple[str, str]] = []
    if content_ids is None:
        content_ids = {s["sentence_id"] for s in _content_sentences(sentences)}
    for frag in fragments:
        for s in sentences:
            if s["sentence_id"] in content_ids and frag in s["text"]:
                out.append((frag, s["sentence_id"]))
                break
    return out


# ============================================================
# 角色判定：LLM 判语义，机械层守上下限
# ============================================================


def _extract_roles(raw: Any, sentences: list[dict[str, Any]]) -> dict[str, str]:
    """LLM 给的逐句角色（**缺一句就中止**）

    为什么必须由模型给：「这句里有没有独立的要求」是**语义问题**。
    机械层按位置猜过一次（「首句 = goal，其余 = fact」），
    代价是**铺垫句进了覆盖率的分母**——「仓库出库的时候」这种状语从句
    被当成一句需求，它抽不出任何东西 → source_coverage 永远 < 100
    → **对真需求假红**（实测 50%）。

    模型没按要求履约 = **履约中止**，不是「用初判凑合」。
    凑合的那份初判正是把真需求判红的那份。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"llm returned {type(raw).__name__}, want dict")
    roles = raw.get("roles")
    if not isinstance(roles, dict):
        raise ValueError(
            "llm output has no 'roles' map —— 句子的角色得由模型判，"
            "不能靠位置猜（首句=goal 会把铺垫句算进覆盖率的分母，"
            "需求盒就对真需求假红）"
        )
    out: dict[str, str] = {}
    missing: list[str] = []
    for s in sentences:
        sid = s["sentence_id"]
        r = roles.get(sid)
        if r not in _ROLES:
            missing.append(sid)
            continue
        out[sid] = r
    if missing:
        raise ValueError(
            f"llm 没给这几句话的角色：{missing} —— 每句都要判"
        )
    return out


def _content_ids_for(
    sentences: list[dict[str, Any]], llm_roles: dict[str, str]
) -> set[str]:
    """覆盖率的分母（内容句的 id 集合）

    = LLM 判的内容句（goal / fact）∪ 含要求表达的句（**机械下限**）

    **下限**：含要求表达的句（必须 / 应当 / 至少 / 不超过 / 要能 / 保证…）
    必须进分母。模型把它叫「只是铺垫」也不作数——那句话确实带着一条要求，
    它没被抽出来就该判红，不该悄悄消失。
    """
    ids: set[str] = set()
    for s in sentences:
        sid = s["sentence_id"]
        role = llm_roles.get(sid, "")
        if role in _CONTENT_ROLES:
            ids.add(sid)
        elif _has_requirement_marker(s.get("text", "")):
            ids.add(sid)
    return ids


def _resolve_roles(
    sentences: list[dict[str, Any]],
    llm_roles: dict[str, str],
    content_ids: set[str],
) -> list[dict[str, Any]]:
    """定稿每句话的角色

    规则：
    - LLM 说 content（goal / fact）→ content
    - LLM 说 context / title 且不在 content_ids 里 → 非内容句（不进分母）
    - 在 content_ids 里但 LLM 没说 content（机械下限拉回来的）→ fact
    """
    out: list[dict[str, Any]] = []
    for s in sentences:
        sid = s["sentence_id"]
        role = llm_roles.get(sid, s.get("role", "fact"))
        if sid in content_ids:
            role = "goal" if role == "goal" else "fact"
        out.append({**s, "role": role})
    return out


def _clean_graph(
    raw: Any,
    sentences: list[dict[str, Any]],
    content_ids: set[str] | None = None,
) -> dict[str, Any]:
    """LLM 图 → 机械清洗后的图

    只收能溯源到真实句子、且文本在原文中出现过的内容——
    幻觉在这一层就被挡掉，不留到 acceptance 才报。

    `content_ids` 是**判定过的内容句集合**（LLM 判角色 + 机械层守下限）。
    不传就退回按当前 role 算——只有直接单测这个函数时才会那样。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"llm returned {type(raw).__name__}, want dict")
    sent_by_id = {s["sentence_id"]: s for s in sentences}
    if content_ids is None:
        content_ids = {s["sentence_id"] for s in _content_sentences(sentences)}
    goal = _goal_text(sentences)

    def ok_fragment(sid: Any, text: Any) -> bool:
        """文本必须是它所溯源那一句的原文子串，且那一句得有内容"""
        src = sent_by_id.get(str(sid or ""))
        frag = (text or "").strip() if isinstance(text, str) else ""
        return bool(
            src
            and src["sentence_id"] in content_ids
            and frag
            and frag in src["text"]
        )

    def str_list(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [x.strip() for x in v if isinstance(x, str) and x.strip()]

    nodes: list[dict[str, Any]] = []
    for c in (raw.get("nodes") or []):
        if not isinstance(c, dict) or not ok_fragment(c.get("trace_to"), c.get("action")):
            continue
        writes = _attach_sources(str_list(c.get("writes")), sentences, content_ids)
        measures = _attach_sources(str_list(c.get("measures")), sentences, content_ids)
        reads = _attach_sources(str_list(c.get("reads")), sentences, content_ids)
        # **节点自己的 guard**：整张图只有一个节点的时候没有边可挂，
        # 约束只能挂在节点上。以前这里永远是空的——于是单节点需求的
        # 约束（"出库必须先出快过期的批次"里的"必须先出…"）整个丢掉，
        # 后面谁也看不见这条规则。
        guard = (c.get("guard") or "").strip() if isinstance(c.get("guard"), str) else ""
        if guard and not ok_fragment(c.get("trace_to"), guard):
            guard = ""
        nodes.append({
            "node_id": f"node_{len(nodes) + 1}",
            "action": (c["action"] or "").strip(),
            "guard": guard,
            "writes": [t for t, _ in writes],
            "reads": [t for t, _ in reads],
            "measures": [t for t, _ in measures],
            "guard_sources": [str(c["trace_to"])] if guard else [],
            "measure_sources": [sid for _, sid in measures if sid],
            "trace_to": str(c["trace_to"]),
        })

    if not nodes:
        # 区分两件都表现为"抽不出东西"的事：
        #   原文压根没有内容句（空表述）→ 盒子声明了 no_deliver 例外，
        #     产出空需求集，交给判据拒。这是正常的业务结果。
        #   原文有内容句、但模型产出全被幻觉校验剔掉 → 模型没履约，中止。
        # 降级删掉之前这两种都被 except 吞成规则版，no_deliver 也就跟着失效了。
        if not content_ids:
            return {"goal": goal, "nodes": [], "edges": []}
        raise ValueError("llm produced no traceable node")

    node_ids = {n["node_id"] for n in nodes}
    edges: list[dict[str, Any]] = []
    for c in (raw.get("edges") or []):
        if not isinstance(c, dict):
            continue
        frm, to = c.get("from"), c.get("to")
        # 边的两端必须指向真实存在的节点——悬空边会让"按位置定类型"失效
        if frm not in node_ids or to not in node_ids or frm == to:
            continue
        guard = (c.get("guard") or "").strip() if isinstance(c.get("guard"), str) else ""
        trigger = (c.get("trigger") or "").strip() if isinstance(c.get("trigger"), str) else ""
        trace = c.get("trace_to")
        # 守卫 / 触发词同样要能在原文里找到，否则视为编造
        if guard and not ok_fragment(trace, guard):
            guard = ""
        if trigger and not ok_fragment(trace, trigger):
            trigger = ""
        edges.append({
            "edge_id": f"edge_{len(edges) + 1}",
            "from": frm,
            "to": to,
            "guard": guard,
            "trigger": trigger,
            "trace_to": str(trace) if trace else "",
        })

    # 原文明写先后顺序而模型漏了边：补成链。图断成一堆孤点就没用了
    if not edges and len(nodes) > 1:
        for a, b in zip(nodes, nodes[1:]):
            edges.append({
                "edge_id": f"edge_{len(edges) + 1}",
                "from": a["node_id"], "to": b["node_id"],
                "guard": "", "trigger": "", "trace_to": b["trace_to"],
            })

    return {"goal": goal, "nodes": nodes, "edges": edges}


def _graph_to_requirements(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """流程图 → 建模盒要的平铺列表（兼容投影，零业务判断）

    这一步纯粹是把图摊平给还没升级的建模盒——它按 requirement_type 分组。
    摊平后的 type 是**由位置推出来的**，不是模型猜的：
      node          → process
      node.writes   → object
      node.measures → metric
      edge.guard    → rule
      edge.trigger  → event
    建模盒升级成直接吃图之后，这个函数就退休。
    """
    out: list[dict[str, Any]] = []
    for n in graph.get("nodes", []):
        trace = n.get("trace_to", "")
        guard_traces = n.get("guard_sources") or []
        measure_traces = n.get("measure_sources") or []
        if n.get("guard"):
            out.append({
                "description": n["guard"], "requirement_type": "rule",
                "trace_to": guard_traces[0] if guard_traces else trace,
            })
        out.append({
            "description": n["action"],
            "requirement_type": "process",
            "trace_to": trace,
        })
        for w in n.get("writes", []):
            out.append({"description": w, "requirement_type": "object", "trace_to": trace})
        for r in n.get("reads", []):
            out.append({"description": r, "requirement_type": "object", "trace_to": trace})
        for i, m in enumerate(n.get("measures", [])):
            out.append({
                "description": m, "requirement_type": "metric",
                "trace_to": measure_traces[i] if i < len(measure_traces) else trace,
            })
    for e in graph.get("edges", []):
        trace = e.get("trace_to", "")
        if e.get("guard"):
            out.append({"description": e["guard"], "requirement_type": "rule", "trace_to": trace})
        if e.get("trigger"):
            out.append({"description": e["trigger"], "requirement_type": "event", "trace_to": trace})
    return out


def _simulate_requirement_extractor(input_data: dict[str, Any]) -> dict[str, Any]:
    """LLM 抽图 + 机械后处理。LLM 不可用就抛，不降级。

    这里曾经有个 except 兜底：任何 LLM 侧问题都退成 `_rule_extract` 的
    词面分类。看着是「投料口不瘫」，实际是开了条绕过质量门禁的通道——
    词面版语义最差，却因为逐句切分不丢句子、每句都挂 trace_to，
    4 条判据（契约/覆盖率/追溯/无幻觉，测的都不是语义质量）必然全过，
    于是拿绿灯、被放行下游。降级不是兜底，是假信号。
    所以这里让异常往上走：executor 会记原因并把 TaskRun 置 FAILED。
    """
    from runtime import llm

    sentences = input_data.get("source_sentences", [])
    goal = _goal_text(sentences)

    # 原文里一句内容都没有（空表述 / 只有标题）→ 没有可抽的东西。
    # **不调 LLM**：让它对着空句子编东西，只会把幻觉引进来，
    # 然后被机械层剔光，最后以「模型失职」的面目中止——
    # 而真相是「业务方什么也没说」。这两件事必须分开。
    if not _content_sentences(sentences):
        result = dict(input_data)
        result.update({
            "business_goal": goal,
            "nodes": [],
            "edges": [],
            "requirements": [],
            "requirement_count": 0,
        })
        return result

    rendered = "\n".join(
        f'{s["sentence_id"]} [{s["role"]}] {s["text"]}' for s in sentences
    )
    # thinking=disabled：这是"照着原文抽结构"，不需要长时间推理。
    # M3 默认开着 think，实测它的 JSON 本体只要 439~1121 字符，<think> 却占
    # 95% 以上（原始输出 27k 字符），会把 max_tokens 用满导致 JSON 轮不到
    # 输出——整轮返回空串，长输入 3 次里空 2 次。关掉后：3 秒、296 token、
    # 正常 JSON（对照 33 秒、8000 token、空输出）。质量不变（4 节点 3 边、
    # coverage 100 实测一致）。
    #
    # **roles 缺了就再问一次**（不是降级）：角色是句子的性质，模型没判
    # 就等于没履约这一项。再问一次还是不给 → 中止。
    prompt = _EXTRACT_PROMPT.format(sentences=rendered)
    raw: Any = None
    llm_roles: dict[str, str] = {}
    last_error: ValueError | None = None
    for _attempt in range(2):
        raw = llm.complete_json(prompt, max_tokens=8000, thinking="disabled")
        try:
            llm_roles = _extract_roles(raw, sentences)
            break
        except ValueError as e:
            last_error = e
            prompt = (
                _EXTRACT_PROMPT.format(sentences=rendered)
                + "\n\n【再强调一次】上一轮的输出里没有给每句话的角色。"
                "这次必须在 roles 字段里给出**每一句**的 sentence_id 和它的角色"
                "（goal / fact / context / title 四选一），一句都不能漏。"
            )
    else:
        raise last_error  # type: ignore[misc]

    # 角色定稿 → 覆盖率的分母（内容句）
    content_ids = _content_ids_for(sentences, llm_roles)
    final_sentences = _resolve_roles(sentences, llm_roles, content_ids)
    graph = _clean_graph(raw, final_sentences, content_ids)
    goal = _goal_text(final_sentences)

    # 图 → 兼容平铺列表，编号按 type 分别计数（建模盒按 type 分组）
    requirements = _graph_to_requirements(graph)
    counters: Counter[str] = Counter()
    for r in requirements:
        rtype = r["requirement_type"]
        counters[rtype] += 1
        r["requirement_id"] = f"req_{_TYPE_PREFIX.get(rtype, 'obj')}_{counters[rtype]}"
        r["priority"] = "must_have"

    result = dict(input_data)
    result.update({
        # **角色定稿要写回 source_sentences**——后面的覆盖率判据读的就是它。
        # 不写回的话，判据读到的还是机械初判（首句=goal、其余=fact），
        # 铺垫句照样进分母，假红原封不动地回来。
        "source_sentences": final_sentences,
        "business_goal": goal,
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "requirements": requirements,
        "requirement_count": len(requirements),
    })
    return result


# ============================================================
# op 3: build_requirement_set —— 机械组装
# ============================================================


def _simulate_requirement_set_builder(input_data: dict[str, Any]) -> dict[str, Any]:
    """组装：补 context、校验必填、保证 id 唯一"""
    result = dict(input_data)
    result.setdefault("context", "")
    result["set_id"] = result.get("set_id") or f"req_set_nl_{uuid4().hex[:8]}"
    # 去重（LLM 可能对同一句产出多条）
    seen: set[tuple[str, str]] = set()
    uniq = []
    for r in result.get("requirements", []):
        key = (r.get("trace_to", ""), (r.get("description") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    result["requirements"] = uniq
    result["requirement_count"] = len(uniq)
    return result


# ============================================================
# op 4: verify_graph_fidelity —— 4 项机械 acceptance
# ============================================================


def _simulate_graph_fidelity_verifier(input_data: dict[str, Any]) -> dict[str, Any]:
    """真算 4 项：契约 / 覆盖 / 溯源 / 幻觉

    全部是集合运算与字符串匹配——不需要模型，结果可复现。
    """
    from runtime.contract import validate_payload
    from boxes.requirement_capture.definition import get_definition

    sentences = input_data.get("source_sentences", [])
    requirements = input_data.get("requirements", [])
    nodes = input_data.get("nodes", [])
    edges = input_data.get("edges", [])
    narrative = re.sub(r"\s+", "", input_data.get("narrative", ""))

    result = dict(input_data)
    details: dict[str, Any] = {}

    # ① 契约：拿**建模盒的输入 schema** 校验
    #    schema 归消费者（建模盒）所有——捕获盒负责满足它，不自己另立一套。
    #    这正是"捕获盒换 LLM 建模盒零改动"的根据。
    from boxes.modeling import get_definition as get_modeling_definition
    contract_schema = get_modeling_definition().get_schema(
        "schema_business_requirement_set"
    )

    # ① 契约：产出的需求集是否符合建模盒的输入契约
    candidate = {
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "context": input_data.get("context", ""),
        "requirements": [
            {
                "requirement_id": r.get("requirement_id", ""),
                "description": r.get("description", ""),
                "requirement_type": r.get("requirement_type", ""),
                "priority": r.get("priority", ""),
            }
            for r in requirements
        ],
    }
    contract_errors = validate_payload(candidate, contract_schema)
    result["contract_compliance"] = not contract_errors
    if contract_errors:
        details["contract_errors"] = contract_errors[:5]

    # ② 溯源：图上每个节点/边都要指向真实句子，边两端要指向真实节点
    #    平铺列表也查一遍——它是图的投影，不该比图更可信
    sent_ids = {s["sentence_id"] for s in sentences}
    node_ids = {n["node_id"] for n in nodes}
    broken = [n["node_id"] for n in nodes if n.get("trace_to") not in sent_ids]
    broken += [e["edge_id"] for e in edges if e.get("trace_to") not in sent_ids]
    broken += [e["edge_id"] for e in edges
               if e.get("from") not in node_ids or e.get("to") not in node_ids]
    broken += [r.get("requirement_id") for r in requirements
               if r.get("trace_to") not in sent_ids]
    result["trace_integrity"] = not broken
    if broken:
        details["broken_traces"] = broken

    # ③ 幻觉：description 必须是原文片段
    hallucinated = [r.get("requirement_id") for r in requirements
                    if re.sub(r"\s+", "", r.get("description", "")) not in narrative]
    result["no_hallucination"] = not hallucinated
    if hallucinated:
        details["hallucinated"] = hallucinated

    # ④ 覆盖：每句内容句都要在图上留下痕迹
    #    节点 action / 节点 guard / 节点 measures / 边 guard / 边 trigger
    #    都被原文的某一句支撑——挂在节点身上的也要单独记账，
    #    否则「一句约束 + 一个动作」这种写法会被误判成没覆盖
    #
    #    分母是**内容句**（goal + fact），不是「除首句外的句子」。
    #    以前把首句（goal）排除在分母外，于是「业务方真正的需求写在
    #    第一句」这件事既不算覆盖也不算漏——需求整个消失了。
    content_ids = {s["sentence_id"] for s in _content_sentences(sentences)}
    traced: set[Any] = set()
    for n in nodes:
        traced.add(n.get("trace_to"))
        traced |= set(n.get("guard_sources", []))
        traced |= set(n.get("measure_sources", []))
    for e in edges:
        traced.add(e.get("trace_to"))
    uncovered = sorted(content_ids - traced)
    result["source_coverage"] = (
        round(100.0 * (len(content_ids) - len(uncovered)) / len(content_ids), 2)
        if content_ids else 0.0
    )
    if uncovered:
        details["uncovered_sentences"] = uncovered

    result["evidence_detail"] = details
    return result


# ============================================================
# op 5: package_requirement_set —— 机械合并
# ============================================================


def _simulate_requirement_set_packager(input_data: dict[str, Any]) -> dict[str, Any]:
    """产出建模盒可直接消费的 RequirementSetPackage"""
    return {
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "context": input_data.get("context", ""),
        "source_sentences": input_data.get("source_sentences", []),
        "nodes": input_data.get("nodes", []),
        "edges": input_data.get("edges", []),
        "requirements": input_data.get("requirements", []),
        "requirement_count": input_data.get("requirement_count", 0),
        "contract_compliance": input_data.get("contract_compliance", False),
        "source_coverage": input_data.get("source_coverage", 0.0),
        "trace_integrity": input_data.get("trace_integrity", False),
        "no_hallucination": input_data.get("no_hallucination", False),
        "evidence_detail": input_data.get("evidence_detail", {}),
    }


# ============================================================
# 注册
# ============================================================

CAPTURE_HANDLERS: dict[str, Any] = {
    "source_sentence_splitter": _simulate_source_sentence_splitter,
    "requirement_extractor": _simulate_requirement_extractor,
    "requirement_set_builder": _simulate_requirement_set_builder,
    "graph_fidelity_verifier": _simulate_graph_fidelity_verifier,
    "requirement_set_packager": _simulate_requirement_set_packager,
}

RUNNER = HandlerRunner(CAPTURE_HANDLERS, owner="wms_requirement_box")
