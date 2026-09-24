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

from runtime.store import TaskRunStore


# ============================================================
# JSON API 数据生成
# ============================================================


def dashboard_data(store: TaskRunStore, box_filter: str | None = None) -> dict[str, Any]:
    """生成看板 JSON 数据

    与 CLI render_dashboard 共用同一份数据源（TaskRunStore）
    """
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
    all_boxes = store.list_boxes()
    if box_filter:
        boxes = [b for b in all_boxes if b == box_filter]
    else:
        boxes = all_boxes

    # Per-box stats（每只 beeBox 的聚合数据，给 3D 渲染用）
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
            "total": b_total,
            "accepted": b_accepted,
            "rejected": b_rejected,
            "acceptance_rate": b_rate,
            "last_run_at": latest["created_at"] if latest else None,
            "last_run_status": latest["acceptance_status"] if latest else None,
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
<title>beeOS Kanban</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; padding: 32px;
    background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%);
    color: #0f172a; min-height: 100vh;
  }}
  h1 {{ margin: 0 0 8px 0; font-size: 26px; font-weight: 700; }}
  .subtitle {{ color: #64748b; font-size: 13px; margin-bottom: 32px; }}

  /* ===== Filter chips ===== */
  .filter-bar {{ margin-bottom: 32px; }}
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
  .refresh-tag {{ float: right; color: #94a3b8; font-size: 11px; }}

  /* ===== 3D beeBox (SVG isometric) ===== */
  .boxes-scene {{
    display: flex; flex-wrap: wrap; gap: 60px;
    margin-bottom: 32px;
    padding: 32px 16px;
    justify-content: center;
  }}
  /* 单盒视图：盒子放大 */
  .boxes-scene.single {{
    padding: 48px 16px 32px;
  }}
  .boxes-scene.single .beebox-svg {{
    width: 440px; height: auto;
  }}
  .beebox-svg {{
    display: block;
    transition: transform 0.3s ease;
    cursor: pointer;
    filter: drop-shadow(0 16px 24px rgba(0,0,0,0.18));
  }}
  .beebox-svg:hover {{
    transform: translateY(-6px) scale(1.02);
  }}
  .beebox-svg .front-bg {{ fill: #ffffff; }}
  .beebox-svg text {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; }}
  .beebox-svg .box-name {{ font-size: 13px; font-weight: 700; fill: #0f172a; }}
  .beebox-svg .box-line {{ font-size: 9px; fill: #64748b; letter-spacing: 0.5px; }}
  .beebox-svg .stat-label {{ font-size: 9px; fill: #64748b; letter-spacing: 0.3px; }}
  .beebox-svg .stat-value {{ font-size: 18px; font-weight: 700; }}
  .beebox-svg .bee-tag {{ font-size: 10px; fill: rgba(255,255,255,0.9); font-weight: 600; letter-spacing: 1px; }}
  .beebox-svg .last-run {{ font-size: 9px; fill: #64748b; }}
  .beebox-svg .last-run-val {{ font-size: 11px; font-weight: 600; }}

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
</style>
</head>
<body>
  <h1>beeOS Kanban — Operational Dashboard</h1>
  <div class="subtitle">
    3D view of beeBoxes in flight · auto-refresh every 2s
    <span class="refresh-tag" id="last-refresh">never</span>
  </div>

  <div class="filter-bar" id="filter-bar"></div>

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

function lineOf(boxId) {{
  if (boxId.includes('modeling') || boxId.includes('development') || boxId.includes('test') || boxId.includes('deploy') || boxId.includes('operation')) return 'software';
  return 'operations';
}}

function healthOf(box) {{
  if (!box || box.total === 0) return 'none';
  const rate = box.acceptance_rate;
  const n = parseFloat(rate);
  if (n >= 80) return 'good';
  if (n >= 50) return 'warn';
  return 'bad';
}}

function renderBeeBox(box, isSingle) {{
  const line = lineOf(box.box_id);
  const health = healthOf(box);
  const safeId = box.box_id.replace(/[^a-zA-Z0-9]/g, '_');

  // 产品线色
  const colors = line === 'operations'
    ? {{ top: ['#1e40af', '#3b82f6'], right: ['#1e3a8a', '#2563eb'] }}
    : {{ top: ['#7c3aed', '#a78bfa'], right: ['#5b21b6', '#7c3aed'] }};

  // 健康度色（仅用于侧条）
  const healthColor = {{ good: '#16a34a', warn: '#f59e0b', bad: '#dc2626', none: '#cbd5e1' }}[health];

  // 单盒视图：盒子名居中放在前面板
  const nameY = isSingle ? 130 : 130;
  const lineY = isSingle ? 155 : 155;

  return `<svg class="beebox-svg" viewBox="0 0 320 220">
    <defs>
      <linearGradient id="top-${{safeId}}" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="${{colors.top[0]}}" />
        <stop offset="100%" stop-color="${{colors.top[1]}}" />
      </linearGradient>
      <linearGradient id="right-${{safeId}}" x1="0%" y1="0%" x2="100%" y2="0%">
        <stop offset="0%" stop-color="${{colors.right[0]}}" />
        <stop offset="100%" stop-color="${{colors.right[1]}}" />
      </linearGradient>
      <pattern id="honey-${{safeId}}" x="0" y="0" width="24" height="20" patternUnits="userSpaceOnUse">
        <polygon points="12,0 24,5 24,15 12,20 0,15 0,5"
                 fill="none" stroke="rgba(255,255,255,0.25)" stroke-width="1" />
      </pattern>
    </defs>

    <!-- Drop shadow -->
    <ellipse cx="160" cy="215" rx="140" ry="6" fill="rgba(0,0,0,0.18)" />

    <!-- Right face -->
    <polygon points="280,40 320,0 320,180 280,220"
             fill="url(#right-${{safeId}})" stroke="rgba(0,0,0,0.15)" stroke-width="0.5" />

    <!-- Top face -->
    <polygon points="0,40 280,40 320,0 40,0"
             fill="url(#top-${{safeId}})" stroke="rgba(0,0,0,0.15)" stroke-width="0.5" />
    <polygon points="0,40 280,40 320,0 40,0"
             fill="url(#honey-${{safeId}})" />

    <!-- Front face -->
    <polygon points="0,40 280,40 280,220 0,220"
             class="front-bg" stroke="rgba(0,0,0,0.15)" stroke-width="0.5" />

    <!-- Health bar (left edge accent) -->
    <rect x="14" y="60" width="5" height="130" fill="${{healthColor}}" rx="2" />

    <!-- Box name (centered on front face) -->
    <text x="140" y="${{nameY}}" class="box-name" text-anchor="middle" font-size="15">${{box.box_id}}</text>
    <text x="140" y="${{lineY}}" class="box-line" text-anchor="middle">${{line.toUpperCase()}} LINE · BEEBOX</text>

    <!-- Bee tag on top face -->
    <text x="20" y="28" class="bee-tag">🐝 beeBox</text>
  </svg>`;
}}

function renderCard(label, value, cssClass) {{
  return `<div class="card">
    <div class="card-label">${{label}}</div>
    <div class="card-value ${{cssClass || ''}}">${{value}}</div>
  </div>`;
}}

function render(data) {{
  const box = data.box_filter;
  const isSingle = !!box;

  document.getElementById('filter-bar').innerHTML = renderFilters(data.boxes, box);
  document.getElementById('last-refresh').textContent =
    'refreshed ' + new Date().toLocaleTimeString();

  let html = '';

  // 3D beeBoxes scene（单盒视图 / 多盒视图）
  if (data.box_stats && data.box_stats.length > 0) {{
    const sceneClass = isSingle ? 'boxes-scene single' : 'boxes-scene';
    const title = isSingle
      ? `beeBox · ${{box}}`
      : `beeBoxes in Flight (${{data.box_stats.length}})`;
    html += `<div class="section"><div class="section-title">${{title}}</div>`;
    html += `<div class="${{sceneClass}}">`;
    for (const b of data.box_stats) {{
      html += renderBeeBox(b, isSingle);
    }}
    html += `</div></div>`;
  }} else {{
    html += `<div class="empty">(no beeBoxes registered)</div>`;
  }}

  document.getElementById('content').innerHTML = html;
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


class KanbanRequestHandler(BaseHTTPRequestHandler):
    """Kanban HTTP handler

    GET /             → HTML page
    GET /api/data     → JSON dashboard data
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
        else:
            self.send_error(404)

    def _serve_html(self) -> None:
        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, box_filter: str | None) -> None:
        data = dashboard_data(self.store, box_filter=box_filter)
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
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