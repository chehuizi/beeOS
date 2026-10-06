"""
kanban.web - Kanban Web 版（HTML + JSON API + 自动刷新）

按 [beebox-design.md §4.2] 实现的运行面板 Web 版。

启动：
  python -m kanban.web --port 8765
  python -m kanban.web --port 8765 --store logs/task_runs.jsonl

然后浏览器打开 http://localhost:8765

设计：
- 内置 http.server（无 web 框架依赖）
- 单文件实现（HTML + CSS + JS 嵌入）
- JS 每 2 秒 fetch /api/data 更新内容（无 full reload）
- API 返回 JSON 数据，前端渲染

URL：
  GET /             → HTML 页面
  GET /api/data     → JSON（boxes / status / acceptance / metrics / recent）
  GET /api/data?box=...  → JSON（过滤单个 box）
  POST /api/trigger → 触发 1 次履约（box_id + task_type + payload）
  POST /api/structure → 自然语言业务表述 → TASK IN 结构化表达（text → payload）
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from kanban.trigger import (
    TriggerError,
    box_domain_context,
    box_meta,
    list_task_entries,
    order_boxes_by_flow,
    registered_box_ids,
    structure_business_text_with_llm,
    trigger_task,
)
from runtime.store import TaskRunStore


# ============================================================
# JSON API 数据生成
# ============================================================


def dashboard_data(store: TaskRunStore, box_filter: str | None = None) -> dict[str, Any]:
    """生成看板 JSON 数据

    与 CLI render_dashboard 共用同一份数据源（TaskRunStore）。
    只展示已注册盒子（BOX_REGISTRY）——未注册盒子的历史记录留在
    审计日志里，看板不显示。
    """
    store = store.view(registered_box_ids())
    records = store._cache
    if box_filter:
        records = [r for r in records if r["box_id"] == box_filter]

    status_counts = dict(Counter(r["status"] for r in records))
    acc_counts = dict(Counter(r["acceptance_status"] or "pending" for r in records))

    # Recent task runs（按 box 过滤）
    recent_all = store.list_recent(limit=10, box_id=box_filter)

    # Exceptions
    if box_filter:
        exceptions = [
            r for r in store._cache
            if r["box_id"] == box_filter and (r["exception_count"] > 0 or r["status"] == "failed")
        ]
        exceptions = sorted(exceptions, key=lambda r: r["created_at"], reverse=True)[:5]
    else:
        exceptions = store.list_exceptions(limit=5)

    # Boxes（按 filter）
    # 排序按业务流向（feeds_into 声明），不是字母序也不是注册序
    all_boxes = order_boxes_by_flow(store.list_boxes())
    if box_filter:
        boxes = [b for b in all_boxes if b == box_filter]
    else:
        boxes = all_boxes

    # Per-box stats（每只 beeBox 的聚合数据，给盒子渲染用）
    # 单盒视图时：只过滤出当前盒；多盒视图时：所有盒
    box_stats = []
    target_boxes = [box_filter] if box_filter else all_boxes
    for b in target_boxes:
        b_records = [r for r in store._cache if r["box_id"] == b]
        b_total = len(b_records)
        b_accepted = sum(1 for r in b_records if r["acceptance_status"] == "accepted")
        b_rejected = sum(1 for r in b_records if r["acceptance_status"] == "rejected")
        b_rate = f"{b_accepted / b_total * 100:.1f}%" if b_total > 0 else "0.0%"
        # 额外：最近一次 task_run + 该盒的 beeline / intent
        latest = next(
            (r for r in sorted(b_records, key=lambda x: x["created_at"], reverse=True)),
            None,
        )
        box_stats.append({
            "box_id": b,
            "meta": box_meta(b),
            "total": b_total,
            "accepted": b_accepted,
            "rejected": b_rejected,
            "acceptance_rate": b_rate,
            "last_run_at": latest["created_at"] if latest else None,
            "last_run_status": latest["acceptance_status"] if latest else None,
            "task_entries": list_task_entries(b),
            "domain_context": box_domain_context(b),
        })

    return {
        "box_filter": box_filter,
        "boxes": boxes,
        "box_stats": box_stats,
        "status_counts": status_counts,
        "acceptance_counts": acc_counts,
        "metrics": store.aggregate_metrics(box_id=box_filter),
        "recent": recent_all,
        "exceptions": exceptions,
    }


# ============================================================
# HTML 页面
# ============================================================


HTML_PAGE = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>BeeBox Fulfillment Overview — beeOS Kanban</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; padding: 32px;
    background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%);
    color: #0f172a; min-height: 100vh;
  }}
  h1 {{ margin: 0 0 8px 0; font-size: 26px; font-weight: 700; }}
  .subtitle {{ color: #64748b; font-size: 13px; }}
  .page-head {{
    display: flex; justify-content: space-between; align-items: flex-start;
    gap: 24px; margin-bottom: 24px;
  }}
  .head-right {{
    display: flex; flex-direction: column; align-items: flex-end; gap: 8px;
    padding-top: 6px;
  }}
  .refresh-tag {{ color: #94a3b8; font-size: 11px; }}

  /* ===== Filter chips ===== */
  .filter-bar {{ }}
  .filter-bar a {{
    display: inline-block; padding: 6px 14px; margin-right: 8px;
    background: white; border: 1px solid #e2e8f0; border-radius: 18px;
    text-decoration: none; color: #475569; font-size: 13px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    transition: all 0.2s;
  }}
  .filter-bar a:hover {{ transform: translateY(-1px); box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
  .filter-bar a.active {{
    background: linear-gradient(135deg, #2563eb, #1d4ed8);
    color: white; border-color: transparent;
  }}

  /* ===== 3D cube beeBox（CSS 3D 玻璃盒） ===== */
  .boxes-scene {{
    display: flex; flex-wrap: wrap; gap: 80px;
    margin-bottom: 32px; padding: 48px 16px 32px;
    justify-content: center;
  }}
  .cube-scene {{
    perspective: 1200px;
    width: var(--cw); height: var(--ch);
    position: relative; flex: 0 0 auto;
  }}
  .cube-float {{
    width: 100%; height: 100%;
    transform-style: preserve-3d;
    animation: cube-float 5s ease-in-out infinite;
  }}
  @keyframes cube-float {{
    0%, 100% {{ transform: translateY(0); }}
    50% {{ transform: translateY(-7px); }}
  }}
  .cube {{
    position: relative; width: 100%; height: 100%;
    transform-style: preserve-3d;
    transition: transform 0.25s ease-out;
  }}
  .cube-face {{
    position: absolute; box-sizing: border-box;
    border: 1px solid var(--edge);
    background: linear-gradient(135deg, rgba(255,255,255,0.16), rgba(255,255,255,0.03) 55%, rgba(255,255,255,0.08));
    box-shadow: inset 0 0 30px rgba(255,255,255,0.05);
  }}
  .cube-front {{
    width: var(--cw); height: var(--ch); left: 0; top: 0;
    transform: translateZ(calc(var(--cd) / 2));
  }}
  /* 玻璃高光斜纹 */
  .cube-front::after {{
    content: ''; position: absolute; inset: 0; pointer-events: none;
    background: linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.15) 42%, transparent 55%);
  }}
  .cube-back {{
    width: var(--cw); height: var(--ch); left: 0; top: 0;
    transform: rotateY(180deg) translateZ(calc(var(--cd) / 2));
    background: linear-gradient(135deg, var(--tint), rgba(255,255,255,0.02));
  }}
  .cube-left {{
    width: var(--cd); height: var(--ch);
    left: calc((var(--cw) - var(--cd)) / 2); top: 0;
    transform: rotateY(-90deg) translateZ(calc(var(--cw) / 2));
  }}
  .cube-right {{
    width: var(--cd); height: var(--ch);
    left: calc((var(--cw) - var(--cd)) / 2); top: 0;
    transform: rotateY(90deg) translateZ(calc(var(--cw) / 2));
    background: linear-gradient(135deg, var(--tint), rgba(255,255,255,0.03));
  }}
  .cube-top {{
    width: var(--cw); height: var(--cd);
    left: 0; top: calc((var(--ch) - var(--cd)) / 2);
    transform: rotateX(90deg) translateZ(calc(var(--ch) / 2));
    background: linear-gradient(135deg, rgba(255,255,255,0.20), var(--tint));
  }}
  .cube-bottom {{
    width: var(--cw); height: var(--cd);
    left: 0; top: calc((var(--ch) - var(--cd)) / 2);
    transform: rotateX(-90deg) translateZ(calc(var(--ch) / 2));
    background: var(--tint);
  }}
  /* 地面软阴影 */
  .cube-shadow {{
    position: absolute; left: 6%; right: 6%; bottom: -30px; height: 30px;
    background: radial-gradient(ellipse at center, rgba(15,23,42,0.22), transparent 70%);
    filter: blur(4px); pointer-events: none;
  }}
  /* 2D 履约盒舞台 */
  #box2d-stage {{
    width: 100%; min-width: 0; flex: 1 1 auto;
    display: flex; align-items: center; justify-content: center;
  }}
  #box2d-stage svg {{ display: block; max-width: 100%; height: auto; }}
  .cube-caption {{ text-align: center; margin-top: 34px; }}
  .cube-caption .name, .beebox-nameplate .name {{ font-size: 15px; font-weight: 700; color: #0f172a; }}
  .cube-caption .name .bid, .beebox-nameplate .name .bid {{
    color: #94a3b8; font-weight: 400; font-size: 11px; margin-left: 6px;
  }}
  .cube-caption .spec {{ display: inline-block; text-align: left; margin-top: 8px; }}
  .spec-row {{ display: flex; font-size: 10px; line-height: 1.8; }}
  .spec-row .k {{
    width: 118px; text-align: right; color: #94a3b8;
    letter-spacing: 0.8px; padding-right: 10px;
  }}
  .spec-row .v {{ color: #334155; font-weight: 600; }}
  .health-dot {{
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    margin-right: 6px; vertical-align: 1px;
  }}

  /* ===== Section titles + tables ===== */
  .section {{ margin-bottom: 32px; }}
  .section-title {{
    font-size: 12px; font-weight: 600; color: #64748b;
    text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px;
  }}

  /* ===== Tables ===== */
  table {{
    width: 100%; border-collapse: collapse; background: white;
    border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  }}
  th, td {{ padding: 8px 12px; text-align: left; font-size: 13px; border-bottom: 1px solid #f1f5f9; }}
  th {{ background: #f1f5f9; font-weight: 600; color: #475569; }}
  tr:last-child td {{ border-bottom: none; }}
  .status-completed {{ color: #2563eb; }}
  .status-failed {{ color: #dc2626; }}
  .acc-accepted {{ color: #16a34a; font-weight: 600; }}
  .acc-rejected {{ color: #dc2626; font-weight: 600; }}
  .acc-pending {{ color: #64748b; }}
  .empty {{
    color: #94a3b8; font-style: italic; padding: 16px;
    background: white; border: 1px solid #e2e8f0; border-radius: 8px;
    text-align: center;
  }}

  /* ===== BeeBox 边界（中间整块区域就是履约盒子） ===== */
  .beebox-frame {{
    border: 2px solid #475569; border-radius: 16px;
    background: rgba(255,255,255,0.45);
    box-shadow: 0 4px 16px rgba(15,23,42,0.06);
  }}
  .beebox-nameplate {{
    display: flex; align-items: baseline; gap: 20px; flex-wrap: wrap;
    padding: 12px 20px; border-bottom: 1.5px solid #64748b;
  }}
  .beebox-nameplate .name {{ font-size: 16px; }}
  .beebox-nameplate .spec {{ display: flex; gap: 18px; margin-top: 0; flex-wrap: wrap; }}
  .beebox-nameplate .spec-row {{ display: flex; gap: 6px; font-size: 10px; line-height: 1.8; }}
  .beebox-nameplate .spec-row .k {{ width: auto; text-align: left; padding-right: 0; }}
  /* ===== DOMAIN CONTEXT：盒子是谁、跟谁接、边界在哪 =====
     它跟下面四栏不是一类东西。四栏讲「这次履约发生了什么」，
     这里讲「这只盒子是什么」。所以是横贯的一条 band，不是第五栏：
     摆在四栏下面会被读成跑出来的第五步，而上下文在履约之前就在那儿。
     样式刻意压低一档（灰底、发丝线、不加边框）——它是背景，不是舞台。 */
  .ctx-band {{
    padding: 9px 20px 11px;
    background: rgba(148,163,184,0.11);
    border-bottom: 1px solid #cbd5e1;
  }}
  .ctx-head {{
    font-size: 9px; font-weight: 700; color: #64748b;
    letter-spacing: 0.9px; margin-bottom: 7px;
  }}
  .ctx-cols {{ display: grid; grid-template-columns: 1.1fr 1fr 1.4fr; gap: 18px; }}
  @media (max-width: 1080px) {{ .ctx-cols {{ grid-template-columns: 1fr; gap: 9px; }} }}
  .ctx-k {{ font-size: 9px; color: #94a3b8; letter-spacing: 0.5px; margin-bottom: 3px; }}
  .ctx-v {{
    font-size: 11px; color: #0f172a; line-height: 1.65;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    word-break: break-word;
  }}
  .ctx-note {{ font-size: 10px; color: #64748b; }}
  .ctx-me {{ color: #b45309; font-weight: 700; }}
  .ctx-hop {{ color: #94a3b8; padding: 0 3px; }}
  /* 四栏：TASK IN · BEELINE · ACCEPTANCE · ARTIFACTS OUT。
     ACCEPTANCE 跟 BEELINE 平级不是排版偏好，是粒度本来就对：中间那栏把 5 个 op
     收成一个黑盒，验收栏把 N 条判据收成一个黑盒，两者粗细一致。
     塞在 BEELINE 那栏里时它跟单个 op 一样大，读起来就成了链上的第 6 步——
     但它判的是整条链，画法比说的话小。 */
  .beebox-body {{
    display: grid;
    grid-template-columns:
      minmax(186px, 0.95fr) minmax(300px, 1.65fr)
      minmax(172px, 0.80fr)  minmax(196px, 1.00fr);
    gap: 12px;
    align-items: stretch; padding: 16px;
    /* 固定盒高：产出内容再多也不把盒子撑长，超出部分各自内部滚动。
       clamp 让矮屏不至于溢出。 */
    height: clamp(380px, 62vh, 560px);
  }}
  .beebox-body > * {{ min-width: 0; }}
  @media (max-width: 1460px) {{
    /* 四栏并排在笔记本屏上会把 BEELINE 的 560 画布压扁 → 2x2 换行。
       盒高放开，否则两行端口撑破格。 */
    .beebox-body {{ grid-template-columns: 1fr 1fr; height: auto; row-gap: 12px; }}
    #box2d-stage, #acc-stage {{ max-width: 600px; margin: 0 auto; }}
  }}
  @media (max-width: 1080px) {{
    /* 单列堆叠时不能沿用固定盒高：三个端口会挤在一格里并溢到盒外 */
    .beebox-body {{ grid-template-columns: 1fr; height: auto; }}
    #task-payload {{ min-height: 200px; max-height: 320px; }}
    .lane {{ padding-bottom: 8px; }}
    /* 单列时整栏通宽，闸跟着撑大会白占掉半屏；钉死成一颗菱形的尺寸 */
    #acc-stage {{ max-width: 240px; }}
  }}
  .port {{
    background: white; border: 1px solid #e2e8f0;
    border-radius: 10px; padding: 14px; align-self: stretch;
    display: flex; flex-direction: column;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
  }}
  /* 中间 BEELINE 流水线框：跟两侧端口对齐，视觉上把盒内工位围起来 */
  .lane {{
    border: 1px dashed #7c93ad; border-radius: 10px; padding: 10px 10px 4px;
    background: rgba(255,255,255,0.55);
    display: flex; flex-direction: column; align-self: stretch;
  }}
  .lane-title {{
    font-size: 11px; font-weight: 700; color: #64748b;
    letter-spacing: 0.8px; margin-bottom: 6px; text-align: center;
  }}
  .lane-ops {{
    font-size: 10px; color: #94a3b8; text-align: center;
    margin-top: 4px; word-break: break-word; line-height: 1.6;
  }}
  /* ACCEPTANCE 栏：跟 BEELINE 同级，但形态刻意不同——边界是实线不是虚线。
     虚线框读起来是「流水线的一段」，而它不是：它是对整条流水线下结论的
     一个判定面，判完了产出才准出 OUT。 */
  .lane-acc {{
    border: 1px solid #d8a23a; border-radius: 10px; padding: 10px 10px 8px;
    background: rgba(255,251,235,0.55);
    display: flex; flex-direction: column; align-self: stretch;
  }}
  .lane-acc .lane-title {{ color: #b45309; }}
  /* 交接方向不靠尖角交代。栏从左到右 IN → BEELINE → ACCEPTANCE → OUT
     本身就在讲顺序，再挂一个指着左边的三角形只是把同一件事说第二遍，
     而且换行布局下它指空、宽屏下它像块渲染毛刺。 */
  .acc-detail {{ flex: 1; min-height: 0; overflow-y: auto; }}
  .acc-hint {{ font-size: 10px; color: #a16207; line-height: 1.7; }}
  .acc-hint code {{
    background: #fef3c7; padding: 0 3px; border-radius: 3px;
    font-size: 9px; color: #92400e;
  }}
  .port-title {{
    font-size: 11px; font-weight: 700; color: #64748b;
    letter-spacing: 0.8px; margin-bottom: 10px;
  }}
  /* 只命中投料口那个 task_type 下拉；产出口里的 type 下拉在更深层，
     被这里 width:100% 命中会把整行撑满、把需求描述挤没。 */
  .port > select {{
    width: 100%; box-sizing: border-box; padding: 6px 8px; margin-bottom: 8px;
    border: 1px solid #cbd5e1; border-radius: 6px; font-size: 13px; background: white;
  }}
  /* 投料框不给人拖动改大小：盒高固定后，拖一把就破版 */
  #task-payload {{ flex: 1 1 auto; min-height: 0; resize: none; }}
  #artifact-slot {{ flex: 1 1 auto; min-height: 0; overflow: auto; }}
  .trigger-row {{
    display: flex; align-items: center; justify-content: center; gap: 10px;
  }}
  .trigger-btn {{
    min-width: 128px; padding: 7px 22px;
    border: none; border-radius: 6px; font-size: 12px;
    background: linear-gradient(135deg, #2563eb, #1d4ed8);
    color: white; cursor: pointer; font-weight: 600;
  }}
  .ghost-btn {{
    padding: 7px 12px; border: 1px dashed #cbd5e1; border-radius: 6px;
    font-size: 11px; background: transparent; color: #64748b; cursor: pointer;
  }}
  .ghost-btn:hover {{ border-color: #94a3b8; color: #334155; }}
  /* 会连用户自己写的字一起清掉的状态——看着就该多点一下鼠标 */
  .ghost-btn.warn {{ border-color: #fca5a5; color: #b91c1c; }}
  .ghost-btn.warn:hover {{ border-color: #ef4444; color: #991b1b; background: #fef2f2; }}
  .trigger-btn:hover {{ opacity: 0.9; }}
  .trigger-btn:disabled {{ opacity: 0.5; cursor: default; }}
  #task-payload {{
    width: 100%; box-sizing: border-box; padding: 8px; margin-bottom: 8px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px; border: 1px solid #cbd5e1; border-radius: 6px;
  }}
  .trigger-result {{ margin-top: 8px; font-size: 12px; color: #475569; min-height: 16px; word-break: break-all; }}
  .trigger-result.ok {{ color: #16a34a; font-weight: 600; }}
  .trigger-result.err {{ color: #dc2626; font-weight: 600; }}

  /* 盒子内部：2D 流水线（矩形工位 + 状态灯 + 方向箭头 + task 令牌） */
  .port-2d {{ fill: #0f172a; stroke-width: 4; }}
  .port-in-2d {{ stroke: #f59e0b; }}
  .port-out-2d {{ stroke: #14b8a6; }}
  .port-label {{ font-size: 9px; fill: #94a3b8; text-anchor: middle; letter-spacing: 1px; }}
  .op-node .station {{ fill: #ffffff; stroke: #94a3b8; stroke-width: 1.4; transition: all 0.25s; }}
  .op-node .lamp {{ fill: #cbd5e1; transition: fill 0.25s; }}
  .op-node text {{ fill: #64748b; transition: fill 0.25s; }}
  .op-node.active .station {{ fill: #dbeafe; stroke: #2563eb; stroke-width: 2; }}
  .op-node.active .lamp {{ fill: #2563eb; }}
  .op-node.active text {{ fill: #1d4ed8; font-weight: 700; }}
  .op-node.done .station {{ fill: #dcfce7; stroke: #16a34a; }}
  .op-node.done .lamp {{ fill: #16a34a; }}
  .op-node.done text {{ fill: #15803d; }}
  .op-node.failed .station {{ fill: #fee2e2; stroke: #dc2626; stroke-width: 2; }}
  .op-node.failed .lamp {{ fill: #dc2626; }}
  .op-node.failed text {{ fill: #b91c1c; font-weight: 700; }}
  .op-link {{ stroke: #cbd5e1; stroke-width: 1.5; }}
  .op-label {{ font-size: 10px; text-anchor: middle; }}
  .token-2d {{ transition: transform 0.32s ease; }}
  .token-2d rect {{
    fill: #f59e0b; stroke: #ffffff; stroke-width: 1.5;
    filter: drop-shadow(0 0 5px rgba(245,158,11,0.8));
  }}

  /* artifacts 出口卡片 */
  .artifact-card {{
    border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px;
    font-size: 12px; background: #f8fafc;
  }}
  .artifact-badge {{
    display: inline-block; padding: 2px 10px; border-radius: 10px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.5px; margin-bottom: 6px;
  }}
  .badge-accepted {{ background: #dcfce7; color: #15803d; }}
  .badge-rejected {{ background: #fee2e2; color: #b91c1c; }}
  .rej-why {{
    font-size: 11px; color: #b91c1c; background: #fff7ed;
    border: 1px solid #fed7aa; border-radius: 6px;
    padding: 5px 8px; margin: 6px 0; line-height: 1.6;
  }}
  /* 需求集（捕获盒产出）+ type 人工确认 */
  .rs-pack {{ margin-top: 8px; border-top: 1px dashed #e2e8f0; padding-top: 8px; }}
  .rs-head {{ font-size: 12px; color: #0f172a; font-weight: 600; margin-bottom: 6px; }}
  .rs-checks {{ display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }}
  .rs-check {{
    font-size: 10px; padding: 2px 6px; border-radius: 4px; border: 1px solid;
  }}
  .rs-check.ok {{ background: #f0fdf4; color: #15803d; border-color: #bbf7d0; }}
  .rs-check.bad {{ background: #fef2f2; color: #b91c1c; border-color: #fecaca; }}
  .rs-check.by {{ background: #f1f5f9; color: #64748b; border-color: #e2e8f0; }}
  .rs-check.warn {{ background: #fffbeb; color: #b45309; border-color: #fde68a; }}
  .rs-degrade {{
    background: #fffbeb; border: 1px solid #fcd34d; color: #92400e;
    padding: 5px 8px; border-radius: 5px; font-size: 11px;
    margin-bottom: 6px; line-height: 1.45; cursor: help;
  }}
  .rs-hint {{ font-size: 10px; color: #94a3b8; margin-bottom: 8px; font-style: italic; }}
  /* 流程图：节点 + 边 + 守卫。竖排单链——看板列窄，横排会被挤断 */
  .fg {{ display: flex; flex-direction: column; align-items: stretch; }}
  .fg-node {{
    display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap;
    padding: 5px 8px; border-radius: 6px; font-size: 12px; line-height: 1.5;
    background: #f0fdf4; border: 1px solid #bbf7d0;
  }}
  .fg-node:hover {{ background: #dcfce7; }}
  .fg-seq {{
    flex: 0 0 auto; min-width: 16px; height: 16px; border-radius: 50%;
    background: #16a34a; color: white; font-size: 10px; font-weight: 700;
    display: inline-flex; align-items: center; justify-content: center;
  }}
  .fg-action {{ color: #0f172a; flex: 1 1 140px; min-width: 0; }}
  .fg-tag {{
    flex: 0 0 auto; font-size: 10px; padding: 1px 5px; border-radius: 3px;
    border: 1px solid transparent; line-height: 1.6;
  }}
  .fg-metric {{ background: #eff6ff; border-color: #bfdbfe; color: #1e40af; }}
  .fg-obj {{ background: #faf5ff; border-color: #e9d5ff; color: #6b21a8; }}
  .fg-edge {{ display: flex; align-items: center; gap: 6px; padding-left: 15px; min-height: 18px; }}
  .fg-line {{ width: 2px; height: 18px; background: #86efac; flex: 0 0 auto; }}
  .fg-guard {{
    font-size: 10px; color: #15803d; background: #f0fdf4;
    border: 1px dashed #86efac; border-radius: 3px; padding: 1px 5px; line-height: 1.6;
  }}
  .fg-dangling .fg-line {{ background: repeating-linear-gradient(
    to bottom, #cbd5e1 0 3px, transparent 3px 6px); }}
  .fg-orphan {{ background: #fef2f2; border-color: #fecaca; }}
  .fg-empty {{ font-size: 11px; color: #94a3b8; font-style: italic; }}
  /* ===== ACCEPTANCE 判据区 =====
     判据条改成竖排：验收栏只有 ~200px 宽，横排四段会折成乱麻，
     折行后「哪条没过」反而读不出来。 */
  .ac-summary {{ font-size: 11px; font-weight: 600; margin-bottom: 6px; }}
  .ac-summary.ok {{ color: #15803d; }}
  .ac-summary.bad {{ color: #b91c1c; }}
  .ac-summary.ac-unknown {{ color: #64748b; font-weight: 400; font-style: italic; }}
  .ac-row {{
    display: grid; grid-template-columns: 10px 1fr; gap: 2px 6px;
    padding: 5px 6px; margin-bottom: 4px; border-radius: 5px;
    background: #fff; border: 1px solid #e2e8f0;
    font-size: 11px; line-height: 1.55;
  }}
  .ac-row.ok {{ border-color: #bbf7d0; }}
  .ac-row.bad {{ border-color: #fecaca; background: #fef2f2; }}
  .ac-mark {{ font-weight: 700; }}
  .ac-row.ok .ac-mark {{ color: #16a34a; }}
  .ac-row.bad .ac-mark {{ color: #dc2626; }}
  .ac-metric {{
    grid-column: 2; color: #0f172a; font-weight: 600;
    font-family: ui-monospace, monospace; font-size: 10px; word-break: break-all;
  }}
  .ac-rule {{
    grid-column: 2; font-size: 10px; color: #64748b; word-break: break-all;
  }}
  .ac-rule::before {{ content: '判据 '; color: #94a3b8; }}
  .ac-actual {{ grid-column: 2; font-size: 10px; color: #475569; word-break: break-all; }}
  .ac-row.bad .ac-actual {{ color: #b91c1c; font-weight: 600; }}
  /* 验收闸：菱形，颜色随判定结果走 */
  /* 菱形不挂状态灯：填充色本身就是状态（待机米 / 判定中黄 / 通过绿 / 没过红），
     再塞一个灯就是同一格两套编码，而且令牌 hold 时停在正中，灯躲不开。 */
  .acc-gate .acc-station {{ fill: #fef3c7; stroke: #f59e0b; stroke-width: 1.4; }}
  .acc-gate.done .acc-station {{ fill: #dcfce7; stroke: #16a34a; }}
  .acc-gate.failed .acc-station {{ fill: #fee2e2; stroke: #dc2626; }}
  .acc-gate.active .acc-station {{ fill: #fef9c3; stroke: #eab308; }}
  .acc-gate text.op-label {{ font-size: 8px; font-weight: 700; fill: #92400e; }}
  .acc-gate.done text.op-label {{ fill: #15803d; }}
  .acc-gate.failed text.op-label {{ fill: #b91c1c; }}
  .rs-trace {{ font-size: 10px; color: #94a3b8; font-family: monospace; flex: 0 0 auto; }}
  /* 纯文本投料框（task_schema 全是标量时）——等宽换成正常字体，读着像在写话 */
  #task-payload.payload-text {{
    font-family: inherit; font-size: 13px; line-height: 1.8; color: #0f172a;
  }}
  /* 接力：把本盒产出投给下游盒，避免手工复制粘贴投错方向 */
  .relay-bar {{
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    margin-top: 8px; padding-top: 8px; border-top: 1px dashed #e2e8f0;
  }}
  .relay-btn {{
    font-size: 11px; padding: 5px 10px; border-radius: 6px; cursor: pointer;
    border: 1px solid #2563eb; background: #eff6ff; color: #1d4ed8;
    font-weight: 600;
  }}
  .relay-btn:hover {{ background: #dbeafe; }}
  .relay-btn:disabled {{ opacity: 0.5; cursor: default; }}
  .relay-note {{ font-size: 10px; color: #64748b; }}
  .badge-failed {{ background: #fee2e2; color: #b91c1c; }}
  .artifact-card .tid {{ color: #94a3b8; font-size: 10px; word-break: break-all; }}
  .artifact-card details {{ margin-top: 6px; }}
  .artifact-card summary {{ cursor: pointer; color: #64748b; font-size: 11px; }}
  .artifact-card pre {{
    font-size: 10px; background: white; border: 1px solid #e2e8f0;
    border-radius: 6px; padding: 6px; overflow-x: auto; max-height: 180px;
  }}

  /* DDD 战术设计视图（业务建模盒的产出物 = 业务模型） */
  .ddd-model {{ margin-top: 8px; border-top: 1px dashed #e2e8f0; padding-top: 8px; }}
  .ddd-goal {{ font-size: 11px; color: #475569; font-style: italic; margin-bottom: 8px; }}
  .ddd-group {{ margin-bottom: 8px; }}
  .ddd-pattern {{
    display: inline-block; font-size: 9px; font-weight: 700; letter-spacing: 0.8px;
    padding: 1px 7px; border-radius: 8px; margin-bottom: 4px;
  }}
  .pat-entity {{ background: #dbeafe; color: #1d4ed8; }}
  .pat-specification {{ background: #ede9fe; color: #6d28d9; }}
  .pat-domain_service {{ background: #ccfbf1; color: #0f766e; }}
  .pat-domain_event {{ background: #fef9c3; color: #a16207; }}
  .pat-domain_metric {{ background: #f1f5f9; color: #475569; }}
  .ddd-el {{
    display: flex; justify-content: space-between; align-items: baseline;
    font-size: 11px; padding: 2px 0 2px 4px;
  }}
  .ddd-name {{ color: #0f172a; font-weight: 600; }}
  .ddd-trace {{
    color: #94a3b8; font-size: 10px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }}
</style>
</head>
<body>
  <div class="page-head">
    <div>
      <h1>BeeBox Fulfillment Overview</h1>
      <div class="subtitle">beeOS Kanban · live view · auto-refresh every 2s</div>
    </div>
    <div class="head-right">
      <span class="refresh-tag" id="last-refresh">never</span>
      <div class="filter-bar" id="filter-bar"></div>
    </div>
  </div>

  <div id="content">Loading...</div>

<script>
const API = '/api/data';

async function fetchData() {{
  const box = new URLSearchParams(location.search).get('box') || '';
  const url = box ? `${{API}}?box=${{encodeURIComponent(box)}}` : API;
  const resp = await fetch(url);
  return await resp.json();
}}

function renderFilters(boxes, current) {{
  let html = `<a href="/" class="${{current ? '' : 'active'}}">All beeBoxes</a>`;
  for (const b of boxes) {{
    const active = current === b ? 'active' : '';
    html += `<a href="/?box=${{encodeURIComponent(b)}}" class="${{active}}">${{b}}</a>`;
  }}
  return html;
}}

// 价值流 → 配色（归属数据来自服务端 box.meta，声明式，不再按 box_id 关键词猜测）
const VS_PALETTE = {{
  'Software Delivery': ['rgba(124,58,237,0.10)', 'rgba(124,58,237,0.40)'],
  'Order Fulfillment': ['rgba(59,130,246,0.10)', 'rgba(37,99,235,0.40)'],
}};

function vsPalette(box) {{
  return VS_PALETTE[(box.meta || {{}}).value_stream] || ['rgba(59,130,246,0.10)', 'rgba(37,99,235,0.40)'];
}}

function healthOf(box) {{
  if (!box || box.total === 0) return 'none';
  const rate = box.acceptance_rate;
  const n = parseFloat(rate);
  if (n >= 80) return 'good';
  if (n >= 50) return 'warn';
  return 'bad';
}}

// 盒子铭牌：Line 是价值流，Box 是业务责任单元
function captionHtml(box) {{
  const meta = box.meta || {{}};
  const healthColor = {{ good: '#16a34a', warn: '#f59e0b', bad: '#dc2626', none: '#cbd5e1' }}[healthOf(box)];
  const row = (k, v) => `<div class="spec-row"><span class="k">${{k}}</span><span class="v">${{v ?? '—'}}</span></div>`;
  return `<div class="name"><span class="health-dot" style="background:${{healthColor}}"></span>${{meta.display_name || box.box_id}}<span class="bid">${{box.box_id}}</span></div>
    <div class="spec">
      ${{row('VALUE STREAM', meta.value_stream)}}
      ${{row('ROLE', meta.role)}}
      ${{row('RUNS', box.total)}}
      ${{row('ACCEPTANCE', box.acceptance_rate)}}
    </div>`;
}}

// ===== 履约盒子空间状态（跨 2s 刷新保持） =====
let _boxState = {{ opStates: {{}}, token: null, artifact: null, running: false, tilt: null }};
let _lastData = null;

function layout2D(ops) {{
  // 蛇形两排布局（盒内 560x420 画布）：row1 左→右（y=150），row2 右→左（y=250）
  // IN 顶行、OUT 底行，各自独占一行，且横向对齐它连的那个工位中心——
  // 端口与工位同 x，接入/产出就是一条竖线，不用猜"这个点连的是哪个框"
  // 间距按完整 op 名设计（不省略），最长 21 字符也能放得下
  const pos = {{}};
  const n = ops.length;
  if (n === 0) return pos;
  const perRow = Math.ceil(n / 2);
  const spread = (count) => {{
    if (count === 1) return [270];
    const arr = [];
    for (let i = 0; i < count; i++) arr.push(Math.round(90 + (360 / (count - 1)) * i));
    return arr;
  }};
  const xs1 = spread(perRow);
  for (let i = 0; i < perRow; i++) pos[ops[i]] = {{ x: xs1[i], y: 150 }};
  const rest = ops.slice(perRow);
  const xs2 = spread(rest.length);
  for (let i = 0; i < rest.length; i++) pos[rest[i]] = {{ x: xs2[rest.length - 1 - i], y: 250 }};
  pos._in = {{ x: pos[ops[0]].x, y: 52 }};
  pos._out = {{ x: pos[ops[n - 1]].x, y: 322 }};
  return pos;
}}

function initBox2D(container, ops) {{
  // BEELINE 栏内部：IN → op1 → ... → opN → OUT。
  // 验收闸不在这里——它是同级的第四栏（见 initAccGate）。
  // 正交布线：横平竖直 + 盒底出口通道，无交叉；令牌沿折线轨道走
  // 盒子边界由外层 .beebox-frame 承担（这里不再画内框）
  // 对外 api：setOpState / moveToken / reset
  const pos = layout2D(ops);
  // 画布高 372：两排工位(150/250) + 标签(284) + OUT(322) + OUT 标签(352)
  let s = `<svg viewBox="0 0 560 372" style="width:100%;height:auto;display:block" preserveAspectRatio="xMidYMid meet">
    <defs>
      <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M 0 1.5 L 8 5 L 0 8.5" fill="none" stroke="#94a3b8" stroke-width="1.6"/>
      </marker>
    </defs>`;

  const chain = ['_in', ...ops, '_out'];
  // 端口与工位同 x 时 L 形会退化成连续重复点，去掉，否则令牌多走一格空动画
  const tidy = (pts) => pts.filter((p, i) =>
    i === 0 || p[0] !== pts[i - 1][0] || p[1] !== pts[i - 1][1]);
  const pathTo = {{}};   // key → 到达该节点的折线途径点（不含终点）
  for (let i = 0; i < chain.length - 1; i++) {{
    const a = pos[chain[i]], b = pos[chain[i + 1]];
    let pts;
    if (chain[i + 1] === '_out' || chain[i] === '_in') {{
      // 进 IN / 出 OUT：垂直落到目标行 → 横向进目标，同 x 时自动退化成一条竖线
      pts = [[a.x, a.y], [a.x, b.y], [b.x, b.y]];
    }} else {{
      pts = [[a.x, a.y], [b.x, b.y]];
    }}
    pts = tidy(pts);
    s += `<polyline points="${{pts.map(p => p.join(',')).join(' ')}}" class="op-link" fill="none" marker-end="url(#arrow)" />`;
    if (chain[i + 1] === '_out') pathTo._out = pts.slice(1, -1);
  }}

  // IN / OUT 端口
  s += `<circle cx="${{pos._in.x}}" cy="${{pos._in.y}}" r="13" class="port-2d port-in-2d" />
    <text x="${{pos._in.x}}" y="${{pos._in.y + 30}}" class="port-label">IN</text>
    <circle cx="${{pos._out.x}}" cy="${{pos._out.y}}" r="13" class="port-2d port-out-2d" />
    <text x="${{pos._out.x}}" y="${{pos._out.y + 30}}" class="port-label">OUT</text>`;

  // 工位：矩形机器 + 右上角状态灯 + 下方标签
  for (const op of ops) {{
    const p = pos[op];
    s += `<g class="op-node" id="op-${{op}}">
      <rect class="station" x="${{p.x - 34}}" y="${{p.y - 17}}" width="68" height="34" rx="8" />
      <circle class="lamp" cx="${{p.x + 26}}" cy="${{p.y - 9}}" r="4" />
      <text x="${{p.x}}" y="${{p.y + 34}}" class="op-label">${{op}}</text>
    </g>`;
  }}

  // task 令牌（包裹）
  s += `<g id="task-token" class="token-2d" style="display:none"><rect x="-9" y="-7" width="18" height="14" rx="3" /></g>`;
  s += '</svg>';
  container.innerHTML = s;

  const token = container.querySelector('#task-token');
  let hideWhenDone = false;
  // 沿折线逐段移动（CSS transition 提供段内平滑）
  const runLegs = (pts, i) => {{
    token.style.transform = `translate(${{pts[i][0]}}px, ${{pts[i][1]}}px)`;
    if (i + 1 < pts.length) {{
      setTimeout(() => runLegs(pts, i + 1), 340);
    }} else if (hideWhenDone) {{
      hideWhenDone = false;
      setTimeout(() => {{ token.style.display = 'none'; }}, 340);
    }}
  }};
  return {{
    setOpState(opId, state) {{
      const g = container.querySelector('#op-' + opId);
      if (g) g.setAttribute('class', 'op-node ' + state);
    }},
    moveToken(key) {{
      if (key === null) {{
        // 若令牌正在折线上走，等走完后隐藏；否则直接隐藏
        hideWhenDone = true;
        setTimeout(() => {{ if (hideWhenDone) {{ hideWhenDone = false; token.style.display = 'none'; }} }}, 400);
        return;
      }}
      const p = pos[key];
      if (!p) return;
      hideWhenDone = false;
      token.style.display = '';
      runLegs([...(pathTo[key] || []), [p.x, p.y]], 0);
    }},
    reset() {{
      for (const op of ops) this.setOpState(op, '');
      hideWhenDone = false;
      token.style.display = 'none';
    }},
  }};
}}

// ===== ACCEPTANCE 栏：菱形闸 + 它自己的令牌 =====
// 它跟 BEELINE 平级（同一层、同样的黑盒粒度），但不是流水线的一步。
// 代码路径本来就分开：evaluate_acceptance 在 BeelineExecutor.execute()
// 返回之后单独调（kanban/trigger.py）。这里不画跨栏连线——
// IN / OUT / ACCEPTANCE 三栏之间本来就没有线，栏的左右顺序已经把
// 「投料 → 算 → 判 → 出」讲完了。
function initAccGate(container) {{
  let s = `<svg viewBox="0 0 200 118" style="width:100%;height:auto;display:block" preserveAspectRatio="xMidYMid meet">
    <g class="acc-gate" id="op-_acc">
      <polygon class="station acc-station" points="100,20 134,52 100,84 66,52" />
      <text x="100" y="106" class="op-label">判定</text>
    </g>
    <g id="acc-token" class="token-2d" style="display:none"><rect x="-8" y="-6" width="16" height="12" rx="3" /></g>
  </svg>`;
  container.innerHTML = s;
  const token = container.querySelector('#acc-token');
  const gate = container.querySelector('#op-_acc');
  return {{
    setState(state) {{
      if (gate) gate.setAttribute('class', 'acc-gate ' + (state || ''));
    }},
    arrive() {{   // 令牌从左边滑进菱形——它是从 BEELINE 栏交接过来的
      token.style.display = '';
      token.style.transition = 'none';
      token.style.transform = 'translate(24px, 52px)';
      void token.offsetWidth;                      // 强制回流，让下面这格是真过渡
      token.style.transition = '';
      token.style.transform = 'translate(100px, 52px)';
    }},
    release() {{  // 放行：令牌往右滑出视野（往 ARTIFACTS OUT 那个方向）
      token.style.transform = 'translate(186px, 52px)';
      setTimeout(() => {{ token.style.display = 'none'; }}, 360);
    }},
    hold() {{     // 没通过：令牌留在闸上，不放行
      token.style.display = '';
      token.style.transform = 'translate(100px, 52px)';
    }},
    reset() {{
      this.setState('');
      token.style.display = 'none';
    }},
  }};
}}

function renderCube(box) {{
  // CSS 3D 玻璃履约盒（仅用于多盒总览的小盒；单盒聚焦视图是 2D 流水线）
  const [tint, edge] = vsPalette(box);
  const tilt = _boxState.tilt || {{ rx: -12, ry: -16 }};

  return `<div>
    <div class="cube-scene" style="--cw:280px;--ch:190px;--cd:130px;">
      <div class="cube-float">
        <div class="cube" style="--tint:${{tint}};--edge:${{edge}};transform:rotateX(${{tilt.rx}}deg) rotateY(${{tilt.ry}}deg)">
          <div class="cube-face cube-back"></div>
          <div class="cube-face cube-left"></div>
          <div class="cube-face cube-right"></div>
          <div class="cube-face cube-top"></div>
          <div class="cube-face cube-bottom"></div>
          <div class="cube-face cube-front"></div>
        </div>
      </div>
      <div class="cube-shadow"></div>
    </div>
    <div class="cube-caption">${{captionHtml(box)}}</div>
  </div>`;
}}

function bindTilt() {{
  // 鼠标跟随倾斜（真实感）；状态存 _boxState.tilt，跨 2s 刷新保持
  const scene = document.querySelector('.cube-scene');
  if (!scene) return;
  scene.addEventListener('mousemove', (e) => {{
    const r = scene.getBoundingClientRect();
    const dx = (e.clientX - r.left) / r.width - 0.5;
    const dy = (e.clientY - r.top) / r.height - 0.5;
    _boxState.tilt = {{ rx: Math.round(-12 - dy * 10), ry: Math.round(-16 + dx * 14) }};
    const cube = scene.querySelector('.cube');
    if (cube) cube.style.transform = `rotateX(${{_boxState.tilt.rx}}deg) rotateY(${{_boxState.tilt.ry}}deg)`;
  }});
  scene.addEventListener('mouseleave', () => {{
    _boxState.tilt = {{ rx: -12, ry: -16 }};
    const cube = scene.querySelector('.cube');
    if (cube) cube.style.transform = 'rotateX(-12deg) rotateY(-16deg)';
  }});
}}

function renderCard(label, value, cssClass) {{
  return `<div class="card">
    <div class="card-label">${{label}}</div>
    <div class="card-value ${{cssClass || ''}}">${{value}}</div>
  </div>`;
}}

// DDD 战术设计视图：result.model_elements 按战术模式分组渲染
function renderDddModel(r) {{
  const m = r && r.model_elements;
  if (!m) return '';
  const PATTERNS = [
    ['entities', 'ENTITY', 'pat-entity'],
    ['specifications', 'SPECIFICATION', 'pat-specification'],
    ['domain_services', 'DOMAIN SERVICE', 'pat-domain_service'],
    ['domain_events', 'DOMAIN EVENT', 'pat-domain_event'],
    ['domain_metrics', 'DOMAIN METRIC', 'pat-domain_metric'],
  ];
  let h = `<div class="ddd-model"><div class="ddd-goal">目标：${{r.business_goal || ''}}</div>`;
  for (const [key, label, cls] of PATTERNS) {{
    const els = m[key] || [];
    if (!els.length) continue;
    h += `<div class="ddd-group"><span class="ddd-pattern ${{cls}}">${{label}}</span>`;
    for (const el of els) {{
      const elName = el.name || el.entity_id || el.rule_id || el.process_id || el.metric_id;
      h += `<div class="ddd-el"><span class="ddd-name">${{elName}}</span><span class="ddd-trace">← ${{el.trace_to || ''}}</span></div>`;
    }}
    h += `</div>`;
  }}
  h += '</div>';
  return h;
}}

  // 流程图（捕获盒产出）——节点 + 边 + 守卫
// 为什么不再有 type 下拉：抽取结果里的类型由**位置**决定，
// 不在 nodes 里就是节点、在 edge.guard 上就是规则、在 measures 上就是指标。
// 位置不是模型猜的标签，所以没有"猜错要人改"这回事——原先那个下拉
// （rule vs metric vs process 实测仍会错）连同它的人工确认一起退休。
function esc(s) {{
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}}

function renderFlowGraph(r) {{
  const nodes = Array.isArray(r.nodes) ? r.nodes : [];
  const edges = Array.isArray(r.edges) ? r.edges : [];
  if (!nodes.length) return '<div class="fg-empty">(未抽出流程节点)</div>';

  // 每个节点的"下一个"是谁：先按 edges 找，没有出边就顺延到链上下一个
  const nxt = {{}};
  edges.forEach((e) => {{ (nxt[e.from] = nxt[e.from] || []).push(e); }});
  const seen = new Set();
  const guardOf = {{}};
  edges.forEach((e) => {{ if (e.guard) guardOf[e.from + '>' + e.to] = e.guard; }});

  function step(n, i) {{
    const outs = nxt[n.node_id] || [];
    const edge = outs[0];
    const guard = (n.guard || (edge && edge.guard) || '');
    const parts = [];
    parts.push(`<div class="fg-node">
      <span class="fg-seq">${{seen.size + 1}}</span>
      <span class="fg-action">${{esc(n.action)}}</span>
      ${{(n.measures || []).map((m) => `<span class="fg-tag fg-metric" title="要量的指标">度量 ${{esc(m)}}</span>`).join('')}}
      ${{(n.writes || []).map((w) => `<span class="fg-tag fg-obj" title="产出实体">产出 ${{esc(w)}}</span>`).join('')}}
      ${{(n.reads || []).map((rd) => `<span class="fg-tag fg-obj" title="读入实体">读入 ${{esc(rd)}}</span>`).join('')}}
      <span class="rs-trace" title="溯源到原文句子">← ${{esc(n.trace_to || '?')}}</span>
    </div>`);
    if (guard) {{
      parts.push(`<div class="fg-edge"><span class="fg-line"></span><span class="fg-guard" title="转移条件">${{esc(guard)}}</span></div>`);
    }} else if (edge) {{
      parts.push('<div class="fg-edge"><span class="fg-line"></span></div>');
    }}
    seen.add(n.node_id);
    if (edge && edge.to && !seen.has(edge.to)) {{
      const next = nodes.find((x) => x.node_id === edge.to);
      if (next) parts.push(step(next));
    }} else if (!edge && i + 1 < nodes.length) {{
      // 断链（没有边）：按原文顺序顺延，画成一条虚线，不假装有边
      parts.push('<div class="fg-edge fg-dangling"><span class="fg-line"></span></div>');
      if (!seen.has(nodes[i + 1].node_id)) parts.push(step(nodes[i + 1], i + 1));
    }}
    return parts.join('');
  }}

  const chain = step(nodes[0], 0);
  // 从 step 里没能走到的节点（分叉出去的）追加在末尾，不静默丢
  const rest = nodes.filter((n) => !seen.has(n.node_id)).map((n) =>
    `<div class="fg-node fg-orphan"><span class="fg-action">${{esc(n.action)}}</span></div>`
  ).join('');

  return `<div class="fg">${{chain}}${{rest}}</div>`;
}}

function renderRequirementSet(r) {{
  if (!r) return '';
  // 4 项判据不在这里画——它们跟上面 ACCEPTANCE 区是同一批数据。
  // 重复一遍会让人误以为这是 beeline 的结论，而不是验收判定。
  const degraded = r.extractor !== 'llm';
  const degradeNote = degraded
    ? `<div class="rs-degrade" title="${{(r.evidence_detail && r.evidence_detail.degrade_reason) || 'LLM 未参与，退回规则抽取'}}">
         ⚠ 降级：规则版抽取（LLM 未参与，守卫和指标靠词面识别，请人工核对）
       </div>`
    : '';
  const nNodes = (r.nodes || []).length;
  const nEdges = (r.edges || []).length;
  return `<div class="rs-pack">
    <div class="rs-head">业务目标：${{esc(r.business_goal || '—')}}</div>
    ${{degradeNote}}
    <div class="rs-checks"><span class="rs-check ${{degraded ? 'by warn' : 'by'}}">抽取者：${{esc(r.extractor)}}</span></div>
    <div class="rs-hint">流程图：${{nNodes}} 个节点${{nEdges ? ` · ${{nEdges}} 条转移` : ''}} —— 类型由所在位置决定，无需逐条确认</div>
    ${{renderFlowGraph(r)}}
  </div>`;
}}

// ===== ACCEPTANCE：跟 TASK IN / BEELINE / ARTIFACTS OUT 平级的第四栏 =====
// 它跟 beeline 不是一回事。beeline 的 op 是「算指标」，
// acceptance 是「拿盒子声明的判据比对，定过不过」——代码路径本来就在
// BeelineExecutor.execute() 返回之后单独调（kanban/trigger.py）。
// 所以盒内画成 IN → BEELINE → ACCEPTANCE → OUT：
// 没过的产出根本不该出现在产出口，也更不该被接力投给下游。
function renderAcceptance(a) {{
  const rows = Array.isArray(a.acceptance_detail) ? a.acceptance_detail : [];
  const acc = a.acceptance_status;
  const ok = acc === 'accepted';
  if (!rows.length) {{
    return `<div class="ac-summary ac-unknown">本次履约没有走 acceptance（${{esc(acc || '未判定')}}）</div>`;
  }}
  const fmt = (v) => {{
    if (v === true) return 'true';
    if (v === false) return 'false';
    if (v === undefined || v === null) return '—';
    if (typeof v === 'number') return String(v);
    return esc(v);
  }};
  const items = rows.map((r) => {{
    const passed = !!r.passed;
    return `<div class="ac-row ${{passed ? 'ok' : 'bad'}}">
      <span class="ac-mark">${{passed ? '✓' : '✗'}}</span>
      <span class="ac-metric">${{esc(r.metric)}}</span>
      <span class="ac-rule">${{esc(r.op)}} ${{fmt(r.expected)}}</span>
      <span class="ac-actual">实测 ${{fmt(r.actual)}}</span>
    </div>`;
  }}).join('');
  const nOk = rows.filter((r) => r.passed).length;
  return `<div class="ac">
    <div class="ac-summary ${{ok ? 'ok' : 'bad'}}">
      ${{ok ? '✓ 全部通过' : '✗ 未通过'}} · ${{nOk}} / ${{rows.length}} 条判据
    </div>
    <div class="ac-rows">${{items}}</div>
  </div>`;
}}

function renderArtifact() {{
  const a = _boxState.artifact;
  if (!a) return `<div class="empty" style="padding:24px 8px;">(等待产出)</div>`;
  const acc = a.acceptance_status || a.status || 'unknown';
  const badge = acc === 'accepted' ? 'badge-accepted' : 'badge-rejected';
  const why = a.rejection_class
    ? `<div class="rej-why">未通过原因：<b>${{REJECT_LABEL[a.rejection_class] || a.rejection_class}}</b></div>`
    : '';
  // 流程图（捕获盒）与 DDD 模型（建模盒）两种产出形态
  const body = Array.isArray(a.result && a.result.nodes)
    ? renderRequirementSet(a.result)
    : renderDddModel(a.result);
  return `<div class="artifact-card">
    <span class="artifact-badge ${{badge}}">${{acc.toUpperCase()}}</span>
    <div class="tid">task_run ${{a.task_run_id}}</div>
    ${{why}}
    ${{body}}
    <details><summary>raw JSON</summary><pre>${{JSON.stringify(a.result, null, 2)}}</pre></details>
  </div>`;
}}

// 判据表落在 ACCEPTANCE 栏，不落在产出口：
// 产出口回答「交出去什么」，验收栏回答「凭什么说它合格」。
// 两处各留一份判据 = 两处都要跟着改，而它们不会一起变。
function renderAcceptanceInto() {{
  const el = document.getElementById('acc-detail');
  if (!el) return;
  const a = _boxState.artifact;
  el.innerHTML = a
    ? renderAcceptance(a)
    : '<div class="empty" style="padding:18px 6px;">(等待判定)</div>';
}}

let _scene2d = null;      // 2D 场景 api（随 shell 持久，不被 2s 刷新重建）
let _accGate = null;      // ACCEPTANCE 栏的闸（跟 _scene2d 同生命周期，独立 api）
let _shellBox = null;   // 当前 shell 属于哪个 box

// rejected 归因 → 中文标签（runtime.models.RejectionClass）
// 文案按「为什么被拒」写，不写「哪个盒子被拒」：同一个类在不同盒子里指的事一样
const REJECT_LABEL = {{
  empty_input: '投料里没有可建模的业务需求',
  insufficient_coverage: '没覆盖住投料里的全部内容',
  conflict: '业务规则之间自相矛盾',
  structural: '结构 / 引用 / 指标不达标',
}};

// ===== DOMAIN CONTEXT =====
// 盒子级的事实，不随单次履约变化。四栏在它下面跑。
// 判据不在这里：声明值和实测值都归 ACCEPTANCE 栏，
// 两处各留一份判据 = 每次改判据要改两个地方。
function renderDomainContext(boxId, ctx) {{
  if (!ctx) return '';
  // 上游 / 下游的显示名由后端随 id 一起给（box_domain_context 里的 _hop）：
  // 单盒过滤时前端只有当前这一只盒的名字表，自己补会露出原始 id
  const hops = [
    ...(ctx.fed_by || []).map((h) => `${{esc(h.name)}}<span class="ctx-hop">→</span>`),
    `<span class="ctx-me">${{esc(ctx.self_name || boxId)}}</span>`,
    ...(ctx.feeds_into || []).map((h) => `<span class="ctx-hop">→</span>${{esc(h.name)}}`),
  ].join(' ') || '<span class="ctx-note">（价值流首端，没有上游）</span>';
  const consumes = (ctx.consumes || []).map((c) => {{
    const req = (c.required || []).map((f) => esc(f.name)).join(' · ') || '—';
    return `<div class="ctx-v">${{esc(c.task_type)}}</div>
            <div class="ctx-v ctx-note">${{esc(c.task_schema)}}　必填 ${{req}}</div>`;
  }}).join('');
  const p = ctx.produces || {{}};
  const outFields = (p.fields || []).map(esc).join(' · ');
  return `<div class="ctx-band">
    <div class="ctx-head">DOMAIN CONTEXT · 领域上下文 — 这只盒子是谁、跟谁接、边界在哪</div>
    <div class="ctx-cols">
      <div>
        <div class="ctx-k">价值流位置</div>
        <div class="ctx-v ctx-note">${{esc(ctx.value_stream)}}</div>
        <div class="ctx-v">${{hops}}</div>
      </div>
      <div>
        <div class="ctx-k">消费契约 · 吃进去什么</div>
        ${{consumes || '<div class="ctx-v ctx-note">—</div>'}}
      </div>
      <div>
        <div class="ctx-k">产出契约 · 吐出来什么</div>
        <div class="ctx-v">${{esc(p.type)}}</div>
        <div class="ctx-v ctx-note">${{esc(p.result_schema)}}</div>
        <div class="ctx-v ctx-note">${{outFields}}</div>
      </div>
    </div>
  </div>`;
}}

function buildShell(boxId, box, entries) {{
  // 静态 shell：只建一次——工位图 DOM 不随 2s 刷新重建
  window._taskEntries = {{}};
  // 接力目标：盒子之间的关系声明在后端 BOX_META.feeds_into（业务信息，不在前端硬编码）
  // 显示名可能尚未就绪（_boxNames 在 refresh 里随后才填），所以重渲时再取一次
  const names = window._boxNames || {{}};
  window._feedsInto = ((box && box.meta && box.meta.feeds_into) || []).map((id) => ({{
    box_id: id,
    display_name: names[id] || id,
  }}));
  let options = '';
  let ops = [];
  let placeholder = '在这里粘贴 task payload（JSON）。下方「填入示例」可取一份样例。';
  for (const e of entries) {{
    window._taskEntries[e.task_type] = e;
    if (e.payload_kind === 'text') {{
      // 换行符必须写成「反斜杠 + n」两个字符：这段 HTML 整体是 Python f-string，
      // 写成真正的转义会被 Python 提前解释成真换行，把 JS 字符串字面量撑破
      // （控制台报 SyntaxError: Invalid or unexpected token）。注释里也别写真转义。
      placeholder = (
        '在这里写业务表述（下方灰字只是示例，履约前请清空）\\n\\n'
        + '首句是业务目标，之后每句是一条业务事实。\\n'
        + '顿号「、」连接并列项时每项会单独成条。\\n\\n'
        + '示例：\\n'
        + 'wms的流程有入库流程、出库流程、盘点流程。\\n'
        + '出库要先进先出拣货。\\n'
        + '入库处理时长要可度量。'
      );
    }}
    options += `<option value="${{e.task_type}}">${{e.task_type}}</option>`;
    if (e.beeline_ops && e.beeline_ops.length > 0) ops = e.beeline_ops;
  }}
  document.getElementById('content').innerHTML = `<div class="section">
    <div class="beebox-frame">
      <div class="beebox-nameplate" id="cube-caption"></div>
      ${{renderDomainContext(boxId, (box && box.domain_context) || null)}}
      <div class="beebox-body">
        <div class="port">
          <div class="port-title" id="port-title">TASK IN · 投料口</div>
          <select id="task-type" onchange="onTaskTypeChange()">${{options}}</select>
          <textarea id="task-payload" rows="12" spellcheck="false"
                    oninput="onPayloadInput()"
                    placeholder="${{placeholder}}"></textarea>
          <div class="trigger-row">
            <button class="trigger-btn" id="trigger-btn" onclick="triggerTask('${{boxId}}')">▶ 履约</button>
            <button class="ghost-btn" id="sample-btn" onclick="toggleSample()">填入示例</button>
          </div>
          <div id="trigger-result" class="trigger-result"></div>
        </div>
        <div class="lane">
          <div class="lane-title">BEELINE · 履约流水线</div>
          <div id="box2d-stage"></div>
          <div class="lane-ops" id="lane-ops"></div>
        </div>
        <div class="lane-acc">
          <div class="lane-title">ACCEPTANCE · 验收闸</div>
          <div id="acc-stage"></div>
          <div class="acc-detail" id="acc-detail"></div>
        </div>
        <div class="port">
          <div class="port-title" id="out-title">ARTIFACTS OUT · 产出口</div>
          <div id="artifact-slot"></div>
        </div>
      </div>
    </div>
  </div>`;
  onTaskTypeChange();

  // 产出口标题按盒子区分（有下游的盒子标"可接力"）
  const outTitle = document.getElementById('out-title');
  if (outTitle) {{
    outTitle.textContent = (window._feedsInto || []).length
      ? 'ARTIFACTS OUT · 产出口（可接力投给下游）'
      : 'ARTIFACTS OUT · 产出口（领域模型）';
  }}

  const stage = document.getElementById('box2d-stage');
  _scene2d = ops.length > 0 ? initBox2D(stage, ops) : null;
  _accGate = initAccGate(document.getElementById('acc-stage'));
  const laneOps = document.getElementById('lane-ops');
  if (laneOps) laneOps.textContent = ops.length > 0 ? ops.join(' · ') : '(该 task type 未绑定 beeline)';
  // shell 重建后恢复工位状态 + 验收闸状态
  if (_scene2d) {{
    for (const op of ops) _scene2d.setOpState(op, _boxState.opStates[op] || '');
  }}
  if (_accGate) _accGate.setState(_boxState.opStates._acc || '');
  _lastArtifactKey = null;   // shell 重建后 slot 是新 DOM，强制重渲 artifact
  renderArtifactInto();
  // 新建的按钮按当前履约状态同步一次：正在跑就禁着，跑完了才能再投
  const btn2 = document.getElementById('trigger-btn');
  if (btn2) btn2.disabled = !!_boxState.running;
}}

let _lastArtifactKey = null;   // 已渲染的 artifact 标识（内容没变就不重建 DOM，保留 details 开合状态）

function renderArtifactInto() {{
  const slot = document.getElementById('artifact-slot');
  if (slot) {{
    const a = _boxState.artifact;
    const key = a ? `${{a.task_run_id}}|${{a.acceptance_status}}` : 'none';
    // 判据表跟着 artifact 一起刷；key 变了才重建，保留 details 开合状态
    if (key !== _lastArtifactKey) {{
      _lastArtifactKey = key;
      slot.innerHTML = renderArtifact() + renderRelayBar();
    }}
  }}
  // 验收栏独立渲染：shell 重建后它是新 DOM，不能靠 _lastArtifactKey 短路
  renderAcceptanceInto();
}}

// 接力：本盒产出 → 下游盒投料口
// 目标盒子来自后端 BOX_META.feeds_into（盒子之间的关系是业务信息，不在前端硬编码）。
// 投料字段按下游 task_schema 裁剪——把捕获盒的 evidence / 抽取者等内部字段
// 一起塞给下游会让人分不清哪些是对方需要的。
function renderRelayBar() {{
  const a = _boxState.artifact;
  const names = window._boxNames || {{}};
  const targets = (window._feedsInto || []).map((t) => ({{
    box_id: t.box_id, display_name: names[t.box_id] || t.box_id,
  }}));
  if (!a || !targets.length) return '';
  // 没验收过的产出不准往下游流——否则下游拿到一份判都没判过的输入，
  // 上游的验收就白做了。REJECTED 时按钮禁用并说明卡在哪条判据上。
  if (a.acceptance_status !== 'accepted') {{
    const failed = (a.acceptance_detail || []).filter((r) => !r.passed)
      .map((r) => r.metric);
    const why = failed.length ? `（未过：${{failed.join('、')}}）` : '';
    return `<div class="relay-bar">
      <button class="relay-btn" disabled>→ 投给下游</button>
      <span class="relay-note">未通过验收，不放行${{why}}</span>
    </div>`;
  }}
  const buttons = targets.map((t) =>
    `<button class="relay-btn" onclick="relayTo('${{t.box_id}}')">→ 投给 ${{t.display_name}}</button>`
  ).join('');
  return `<div class="relay-bar">${{buttons}}<span class="relay-note">一键投料，不用手工复制 JSON</span></div>`;
}}

function relayTo(boxId) {{
  const a = _boxState.artifact;
  if (!a) return;
  // 双保险：按钮已禁用之外，函数入口再挡一次——
  // URL 里塞 relay 参数也能绕过 UI
  if (a.acceptance_status !== 'accepted') {{
    const failed = (a.acceptance_detail || []).filter((r) => !r.passed)
      .map((r) => r.metric);
    window.alert(
      '这份产出没通过验收，不能投给下游。'
      + (failed.length ? `\\n未通过的判据：${{failed.join('、')}}` : '')
    );
    return;
  }}
  // 投料口径由下游盒子的 task_schema 决定：
  // 下游只要 set_id / business_goal / requirements（建模盒的输入契约），
  // 不把本盒的 evidence / extractor 之类内部字段塞过去
  const p = a.result || {{}};
  const payload = {{
    set_id: p.set_id, business_goal: p.business_goal,
    context: p.context || '', requirements: p.requirements || [],
  }};
  location.href = `/?box=${{boxId}}&relay=` + encodeURIComponent(JSON.stringify(payload));
}}

// 接力回填：从 URL 的 relay 参数还原投料内容到投料框
function applyRelayFromUrl() {{
  const m = location.search.match(/[?&]relay=([^&]*)/);
  if (!m) return;
  let payload;
  try {{
    payload = JSON.parse(decodeURIComponent(m[1]));
  }} catch (e) {{
    return;
  }}
  const el = document.getElementById('task-payload');
  const res = document.getElementById('trigger-result');
  if (!el) return;
  onTaskTypeChange();   // 先按 payload_kind 铺好形态
  el.value = JSON.stringify(payload, null, 2);
  _sampleBaseline = '';   // 接力来的是真实内容，不是示例 → 按钮该是「清空」
  if (res) {{
    res.className = 'trigger-result ok';
    res.textContent = `已接力 ${{payload.requirements ? payload.requirements.length : 0}} 条需求`
      + ' — 确认无误点「履约」';
  }}
  // 清掉 URL 里的 relay，避免 2s 刷新后误判为未履约
  history.replaceState(null, '', location.pathname + location.search.replace(/&?relay=[^&]*/, ''));
}}

function updateCaption(box) {{
  const el = document.getElementById('cube-caption');
  if (!el) return;
  el.innerHTML = captionHtml(box);
}}

// 最近一次由按钮写入的示例原文，用来区分「框里是原样示例」还是「用户改过」。
// 声明必须在 onTaskTypeChange 之前——TDZ 会让先执行的赋值直接报错。
let _sampleBaseline = '';

function onTaskTypeChange() {{
  const typeEl = document.getElementById('task-type');
  const payloadEl = document.getElementById('task-payload');
  if (!typeEl || !payloadEl) return;
  const entry = (window._taskEntries || {{}})[typeEl.value] || {{}};
  const sample = entry.sample_payload || {{}};

  // 投料框形态由后端按 task_schema 形状决定（见 kanban/trigger._payload_kind）：
  // 纯文本 task 直接打字；嵌套结构 task 才用 JSON 编辑器。
  // 不在前端硬编码——加新盒子时不用改这里。
  const isText = entry.payload_kind === 'text';
  payloadEl.classList.toggle('payload-text', isText);
  // 故意不预填示例：预填内容看着像"已经有人在投料"，直接点履约会误以为
  // 那就是自己的数据。示例留在 placeholder（灰字，空框才显示）里，
  // 另给一个「填入示例 / 清空」切换按钮按需取用。
  payloadEl.value = '';
  _sampleBaseline = '';   // 换了 task type，旧的示例基准不再作数
  syncSampleBtn();

  const t = document.getElementById('port-title');
  if (t) t.textContent = isText
    ? 'TASK IN · 投料口（自然语言业务表述）'
    : 'TASK IN · 投料口（结构化表达）';
}}

function sampleTextOf(entry) {{
  const sample = entry.sample_payload || {{}};
  return entry.payload_kind === 'text'
    ? (sample.narrative || sample.text || '')
    : JSON.stringify(sample, null, 2);
}}

// 按钮就两态：空 → 填入示例，有内容 → 清空。
// 不再细分「清掉示例」——文案越细越像在说一件它做不到的事。
// 真正的保护不靠措辞：框里不是原封不动的示例时，清空前先问一句。
// 判据是 _sampleBaseline（按钮写入时留一份原文快照），跟文案无关。

function isPristineSample() {{
  const payloadEl = document.getElementById('task-payload');
  if (!payloadEl) return false;
  const cur = payloadEl.value;
  return !!_sampleBaseline && cur === _sampleBaseline;
}}

function toggleSample() {{
  const typeEl = document.getElementById('task-type');
  const payloadEl = document.getElementById('task-payload');
  if (!typeEl || !payloadEl) return;
  const entry = (window._taskEntries || {{}})[typeEl.value] || {{}};

  if (!payloadEl.value.trim()) {{
    _sampleBaseline = sampleTextOf(entry);
    payloadEl.value = _sampleBaseline;
  }} else {{
    // 只清原封不动的示例 → 可逆，直接撤。
    // 清用户自己写的内容 → 不可逆，先问一句，不给误点留口子。
    if (!isPristineSample()
        && !window.confirm('清空投料框？框里的内容会全部丢失，无法撤销。')) return;
    _sampleBaseline = '';
    payloadEl.value = '';
  }}
  clearPayloadError();
  syncSampleBtn();
  payloadEl.focus();
}}

function syncSampleBtn() {{
  const payloadEl = document.getElementById('task-payload');
  const btn = document.getElementById('sample-btn');
  if (!payloadEl || !btn) return;
  const has = !!payloadEl.value.trim();
  btn.textContent = has ? '清空' : '填入示例';
  if (!has) {{
    btn.title = '把示例填进投料框';
    btn.classList.remove('warn');
  }} else if (isPristineSample()) {{
    btn.title = '撤掉刚填进去的示例，回到空框';
    btn.classList.remove('warn');
  }} else {{
    btn.title = '清掉整个投料框，包括你自己写的内容（不可撤销，会先问一句）';
    btn.classList.add('warn');
  }}
}}

function onPayloadInput() {{
  // 一开始打字就把上一条报错撤掉，否则红字挂着会误导成"当前内容仍无效"
  clearPayloadError();
  syncSampleBtn();
}}

// 一开始打字/填示例就把上一条报错撤掉，否则红字挂着会误导成"当前内容仍无效"
function clearPayloadError() {{
  const res = document.getElementById('trigger-result');
  if (res && res.classList.contains('err')) {{
    res.className = 'trigger-result';
    res.textContent = '';
  }}
}}

function sleep(ms) {{ return new Promise(r => setTimeout(r, ms)); }}

async function triggerTask(boxId) {{
  const resultEl = document.getElementById('trigger-result');
  const btn = document.getElementById('trigger-btn');
  const taskType = document.getElementById('task-type').value;
  const entry = (window._taskEntries || {{}})[taskType] || {{}};
  const raw = document.getElementById('task-payload').value;
  let payload;
  if (entry.payload_kind === 'text') {{
    // 纯文本 task：整段就是 narrative，套一层 JSON 是折磨
    if (!raw.trim()) {{
      resultEl.className = 'trigger-result err';
      resultEl.textContent = '先写业务表述再履约';
      return;
    }}
    payload = {{ narrative: raw }};
  }} else {{
    try {{
      payload = JSON.parse(raw);
    }} catch (e) {{
      resultEl.className = 'trigger-result err';
      resultEl.textContent = 'payload JSON 解析失败: ' + e.message;
      return;
    }}
  }}
  _boxState.running = true;
  btn.disabled = true;
  resultEl.className = 'trigger-result';
  resultEl.textContent = '履约中...';
  try {{
    const resp = await fetch('/api/trigger', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{box_id: boxId, task_type: taskType, payload: payload}}),
    }});
    const data = await resp.json();
    if (!resp.ok) {{
      resultEl.className = 'trigger-result err';
      // 400 = 投料被第一道闸（结构契约）拦下，这次投料没有产生 TaskRun
      const prefix = resp.status === 400 ? '投料被拒（未进入履约）: ' : '触发失败: ';
      resultEl.textContent = prefix + (data.error || resp.status);
    }} else {{
      // 在 2D 盒子内部回放真实执行轨迹（op_trace 来自服务端真实执行）
      const trace = data.op_trace || [];
      _boxState.opStates = {{}};
      _boxState.artifact = null;
      renderArtifactInto();
      if (_scene2d) {{
        _scene2d.reset();
        _scene2d.moveToken('_in');
      }}
      if (_accGate) _accGate.reset();
      await sleep(600);
      for (const step of trace) {{
        _boxState.opStates[step.op_id] = 'active';
        if (_scene2d) {{
          _scene2d.setOpState(step.op_id, 'active');
          _scene2d.moveToken(step.op_id);
        }}
        await sleep(600);
        const st = step.status === 'completed' ? 'done' : 'failed';
        _boxState.opStates[step.op_id] = st;
        if (_scene2d) _scene2d.setOpState(step.op_id, st);
      }}
      // beeline 走完 → 把令牌交给 BEELINE 栏右边的验收闸（第四栏）。
      // 闸自己亮灯：过 → 放行到 OUT；不过 → 令牌停在闸上，产出不出。
      _boxState.opStates._acc = 'active';
      if (_scene2d && trace.length) _scene2d.moveToken(trace[trace.length - 1].op_id);
      if (_accGate) {{
        _accGate.setState('active');
        _accGate.arrive();
      }}
      await sleep(700);
      const accPassed = data.acceptance_status === 'accepted';
      _boxState.opStates._acc = accPassed ? 'done' : 'failed';
      if (_accGate) _accGate.setState(_boxState.opStates._acc);
      if (accPassed) {{
        // 过了闸才放产出出去
        if (_accGate) _accGate.release();
        if (_scene2d) _scene2d.moveToken('_out');
        await sleep(600);
      }} else if (_accGate) {{
        _accGate.hold();
      }}
      if (_scene2d) _scene2d.moveToken(null);
      _boxState.artifact = {{
        task_run_id: data.task_run_id,
        status: data.status,
        acceptance_status: data.acceptance_status,
        acceptance_detail: data.acceptance_detail || [],
        rejection_class: data.rejection_class,
        result: data.result,
      }};
      renderArtifactInto();
      resultEl.className = 'trigger-result ' + (data.acceptance_status === 'accepted' ? 'ok' : 'err');
      const why = data.rejection_class ? ` — ${{REJECT_LABEL[data.rejection_class] || data.rejection_class}}` : '';
      resultEl.textContent = `task_run ${{data.task_run_id.slice(0, 8)}}… → ${{data.status}} / ${{data.acceptance_status || 'n/a'}}${{why}}`;
    }}
  }} catch (e) {{
    resultEl.className = 'trigger-result err';
    resultEl.textContent = '请求失败: ' + e.message;
  }}
  _boxState.running = false;
  // 按 id 重取，别用函数入口捕获的那个引用：
  // 履约要跑几十秒（LLM 那条腿），期间 tick() 可能重建 shell 把按钮换掉，
  // 解锁旧节点等于没解——按钮就永久卡在 disabled，投料口瘫掉
  const btnNow = document.getElementById('trigger-btn');
  if (btnNow) btnNow.disabled = false;
  tick();
}}

function render(data) {{
  _lastData = data;
  const box = data.box_filter;
  const isSingle = !!box;

  document.getElementById('filter-bar').innerHTML = renderFilters(data.boxes, box);
  document.getElementById('last-refresh').textContent =
    'refreshed ' + new Date().toLocaleTimeString();

  // 单盒视图：静态 shell + 动态小更新（WebGL 场景常驻）
  if (isSingle && data.box_stats && data.box_stats.length > 0) {{
    // 盒子显示名表（接力按钮要用）
    window._boxNames = {{}};
    for (const b of data.box_stats) {{
      window._boxNames[b.box_id] = (b.meta && b.meta.display_name) || b.box_id;
    }}
    if (_shellBox !== box) {{
      buildShell(box, data.box_stats[0], data.box_stats[0].task_entries || []);
      _shellBox = box;
      applyRelayFromUrl();
    }}
    updateCaption(data.box_stats[0]);
    renderArtifactInto();
    return;
  }}

  // 多盒视图 / 空态：重建 DOM（CSS 立方体，无 WebGL 状态）
  _shellBox = null;
  _scene2d = null;
  _accGate = null;
  let html = '';
  if (data.box_stats && data.box_stats.length > 0) {{
    html += `<div class="section"><div class="section-title">beeBoxes in Flight (${{data.box_stats.length}})</div>`;
    html += `<div class="boxes-scene">`;
    for (const b of data.box_stats) {{
      html += renderCube(b);
    }}
    html += `</div></div>`;
  }} else {{
    html += `<div class="empty">(no beeBoxes registered)</div>`;
  }}
  document.getElementById('content').innerHTML = html;
  bindTilt();
}}

async function tick() {{
  try {{
    const data = await fetchData();
    render(data);
  }} catch (e) {{
    document.getElementById('content').innerHTML =
      `<div class="empty">Failed to fetch: ${{e.message}}</div>`;
  }}
}}

tick();
setInterval(tick, 2000);
</script>
</body>
</html>
"""


# ============================================================
# HTTP handler
# ============================================================


VENDOR_DIR = Path(__file__).resolve().parent / "vendor"


class KanbanRequestHandler(BaseHTTPRequestHandler):
    """Kanban HTTP handler

    GET /             → HTML page
    GET /api/data     → JSON dashboard data
    GET /vendor/*     → 本地化前端依赖（three.js 等）
    POST /api/trigger → 触发 1 次履约（{box_id, task_type, payload}）
    """

    # 注入 store 实例（在 main 时设置）
    store: TaskRunStore = None  # type: ignore[assignment]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/" or parsed.path == "/index.html":
            self._serve_html()
        elif parsed.path == "/api/data":
            box_filter = (qs.get("box") or [None])[0] or None
            self._serve_json(box_filter)
        elif parsed.path.startswith("/vendor/"):
            self._serve_vendor(parsed.path[len("/vendor/"):])
        else:
            self.send_error(404)

    def _serve_vendor(self, rel: str) -> None:
        """静态服务 kanban/vendor/ 下的前端依赖（防路径穿越）"""
        target = (VENDOR_DIR / rel).resolve()
        if not str(target).startswith(str(VENDOR_DIR)) or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        mime = "text/javascript; charset=utf-8" if target.suffix == ".js" else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/trigger", "/api/structure"):
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        if parsed.path == "/api/structure":
            # 自然语言业务表述 → 结构化表达
            # 走**捕获盒的 task**（不再是影子函数）——每一次抽取都产生 TaskRun、
            # 都过 4 项 acceptance、失败有 rejection_class 归因
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                self._send_json(400, {"error": "body must include text (non-empty string)"})
                return
            try:
                result = trigger_task(
                    "requirement_capture_box", "capture_business_requirement",
                    {"narrative": text}, self.store,
                )
            except TriggerError as e:
                self._send_json(400, {
                    "error": str(e), "stage": "intake", "task_run_created": False,
                })
                return
            except Exception as e:
                self._send_json(500, {"error": f"capture failed: {e}"})
                return

            pkg = result.get("result") or {}
            self._send_json(200, {
                "task_run_id": result.get("task_run_id"),
                "status": result.get("status"),
                "acceptance_status": result.get("acceptance_status"),
                "acceptance_detail": result.get("acceptance_detail") or [],
                "rejection_class": result.get("rejection_class"),
                "op_trace": result.get("op_trace"),
                "payload": pkg,
                "requirement_count": pkg.get("requirement_count", 0),
                "extractor": pkg.get("extractor", "rule"),
                "note": (pkg.get("evidence_detail") or {}).get("degrade_reason") or "",
            })
            return

        box_id = body.get("box_id")
        task_type = body.get("task_type")
        payload = body.get("payload")
        if not box_id or not task_type or not isinstance(payload, dict):
            self._send_json(400, {
                "error": "body must include box_id, task_type, and payload (object)",
            })
            return

        try:
            result = trigger_task(box_id, task_type, payload, self.store)
        except TriggerError as e:
            # TriggerError = 投料被拒（box/task/beeline 未注册，或结构契约没过）。
            # 这些都发生在 create_task_run 之前——本次投料不产生 TaskRun，
            # 看板 / store 里不会留下记录。用 stage 字段让前端能区分归因。
            self._send_json(400, {
                "error": str(e),
                "stage": "intake",
                "task_run_created": False,
            })
            return
        except Exception as e:
            self._send_json(500, {"error": f"fulfillment failed: {e}"})
            return

        self._send_json(200, result)

    def _serve_html(self) -> None:
        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, box_filter: str | None) -> None:
        data = dashboard_data(self.store, box_filter=box_filter)
        self._send_json(200, data)

    def _send_json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # 简化日志输出
        print(f"[kanban] {self.address_string()} - {format % args}")


# ============================================================
# Server 启动
# ============================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="beeOS Kanban Web dashboard")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default 127.0.0.1, use 0.0.0.0 for external access)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bind port (default 8765)",
    )
    parser.add_argument(
        "--store",
        default="logs/task_runs.jsonl",
        help="TaskRun JSONL store path",
    )
    args = parser.parse_args(argv)

    store = TaskRunStore(path=args.store)
    KanbanRequestHandler.store = store

    server = ThreadingHTTPServer((args.host, args.port), KanbanRequestHandler)
    print(f"beeOS Kanban Web running at http://{args.host}:{args.port}")
    print(f"  store: {args.store}")
    print(f"  press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down...")
        server.shutdown()
        return 0


if __name__ == "__main__":
    sys.exit(main())