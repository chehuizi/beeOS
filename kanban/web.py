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

    return {
        "box_filter": box_filter,
        "boxes": boxes,
        "status_counts": status_counts,
        "acceptance_counts": acc_counts,
        "metrics": store.aggregate_metrics(box_id=box_filter),
        "recent": recent_all,
        "exceptions": exceptions,
    }


# ============================================================
# HTML 页面
# ============================================================


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>beeOS Kanban</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; padding: 24px; background: #f8fafc; color: #0f172a;
  }}
  h1 {{ margin: 0 0 8px 0; font-size: 22px; }}
  .subtitle {{ color: #64748b; font-size: 13px; margin-bottom: 24px; }}
  .section {{ margin-bottom: 24px; }}
  .section-title {{
    font-size: 12px; font-weight: 600; color: #64748b;
    text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;
  }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
  .card {{
    background: white; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 16px;
  }}
  .card-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; }}
  .card-value {{ font-size: 24px; font-weight: 600; margin-top: 4px; }}
  .card-value.accepted {{ color: #16a34a; }}
  .card-value.rejected {{ color: #dc2626; }}
  .card-value.completed {{ color: #2563eb; }}
  .card-value.failed {{ color: #dc2626; }}
  table {{
    width: 100%; border-collapse: collapse; background: white;
    border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden;
  }}
  th, td {{ padding: 8px 12px; text-align: left; font-size: 13px; border-bottom: 1px solid #f1f5f9; }}
  th {{ background: #f1f5f9; font-weight: 600; color: #475569; }}
  tr:last-child td {{ border-bottom: none; }}
  .status-completed {{ color: #2563eb; }}
  .status-failed {{ color: #dc2626; }}
  .acc-accepted {{ color: #16a34a; font-weight: 600; }}
  .acc-rejected {{ color: #dc2626; font-weight: 600; }}
  .acc-pending {{ color: #64748b; }}
  .empty {{ color: #94a3b8; font-style: italic; padding: 12px; background: white; border: 1px solid #e2e8f0; border-radius: 8px; }}
  .filter-bar {{ margin-bottom: 16px; }}
  .filter-bar a {{
    display: inline-block; padding: 4px 12px; margin-right: 8px;
    background: white; border: 1px solid #e2e8f0; border-radius: 16px;
    text-decoration: none; color: #475569; font-size: 12px;
  }}
  .filter-bar a.active {{ background: #2563eb; color: white; border-color: #2563eb; }}
  .refresh-tag {{ float: right; color: #94a3b8; font-size: 11px; }}
</style>
</head>
<body>
  <h1>beeOS Kanban — Operational Dashboard</h1>
  <div class="subtitle">
    Running view of beeBox task runs · auto-refresh every 2s
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

function renderCard(label, value, cssClass) {{
  return `<div class="card">
    <div class="card-label">${{label}}</div>
    <div class="card-value ${{cssClass || ''}}">${{value}}</div>
  </div>`;
}}

function renderFilters(boxes, current) {{
  let html = `<a href="/" class="${{current ? '' : 'active'}}">All boxes</a>`;
  for (const b of boxes) {{
    const active = current === b ? 'active' : '';
    html += `<a href="/?box=${{encodeURIComponent(b)}}" class="${{active}}">${{b}}</a>`;
  }}
  return html;
}}

function render(data) {{
  const box = data.box_filter;

  document.getElementById('filter-bar').innerHTML = renderFilters(data.boxes, box);
  document.getElementById('last-refresh').textContent =
    'refreshed ' + new Date().toLocaleTimeString();

  let html = '';

  // Status counts
  html += `<div class="section"><div class="section-title">Status Counts</div><div class="cards">`;
  const statusKeys = Object.keys(data.status_counts);
  if (statusKeys.length === 0) {{
    html += `<div class="empty">(no task runs)</div>`;
  }} else {{
    for (const k of statusKeys.sort()) {{
      html += renderCard(k, data.status_counts[k], k);
    }}
  }}
  html += `</div></div>`;

  // Acceptance
  html += `<div class="section"><div class="section-title">Acceptance Outcomes</div><div class="cards">`;
  const accKeys = Object.keys(data.acceptance_counts);
  if (accKeys.length === 0) {{
    html += `<div class="empty">(no acceptance results)</div>`;
  }} else {{
    for (const k of accKeys.sort()) {{
      html += renderCard(k, data.acceptance_counts[k], k);
    }}
  }}
  html += `</div></div>`;

  // Aggregate metrics
  const m = data.metrics;
  html += `<div class="section"><div class="section-title">Aggregate Metrics</div><div class="cards">`;
  for (const [k, v] of Object.entries(m)) {{
    html += renderCard(k, v);
  }}
  html += `</div></div>`;

  // Recent task runs
  html += `<div class="section"><div class="section-title">Recent Task Runs</div>`;
  if (data.recent.length === 0) {{
    html += `<div class="empty">(no task runs yet)</div>`;
  }} else {{
    html += `<table>
      <thead><tr>
        <th>task_run_id</th><th>box</th><th>status</th>
        <th>acceptance</th><th>duration</th><th>ops</th>
      </tr></thead><tbody>`;
    for (const r of data.recent) {{
      const dur = r.duration_ms > 0 ? r.duration_ms + 'ms' : '<1ms';
      const acc = r.acceptance_status || 'pending';
      html += `<tr>
        <td>${{r.task_run_id.slice(0, 12)}}</td>
        <td>${{r.box_id}}</td>
        <td class="status-${{r.status}}">${{r.status}}</td>
        <td class="acc-${{acc}}">${{acc}}</td>
        <td>${{dur}}</td>
        <td>${{r.op_count}}</td>
      </tr>`;
    }}
    html += `</tbody></table>`;
  }}
  html += `</div>`;

  // Active exceptions
  html += `<div class="section"><div class="section-title">Active Exceptions</div>`;
  if (data.exceptions.length === 0) {{
    html += `<div class="empty">(no active exceptions)</div>`;
  }} else {{
    html += `<table><thead><tr><th>task_run_id</th><th>box</th><th>status</th><th>exceptions</th></tr></thead><tbody>`;
    for (const r of data.exceptions) {{
      html += `<tr>
        <td>${{r.task_run_id.slice(0, 8)}}</td>
        <td>${{r.box_id}}</td>
        <td class="status-${{r.status}}">${{r.status}}</td>
        <td>${{r.exception_count}}</td>
      </tr>`;
    }}
    html += `</tbody></table>`;
  }}
  html += `</div>`;

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