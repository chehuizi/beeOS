"""M0 demo FastAPI server（无 PG / 无持久化）。

路由：
  GET  /                     BeeBox+Bee 中心化的 demo 页
  GET  /api/v0/boxes         列出已注册 Box
  GET  /api/v0/boxes/{type}  取 Box manifest
  POST /api/v0/run           跑一个 Box 任务（同步返回）
  GET  /api/v0/audit         读最近 N 条审计
  GET  /health               健康检查

启动：
  uvicorn bee.server:app --host 0.0.0.0 --port 8085
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from bee import Bee, list_supported
from bee.audit import LocalAuditLog
from bee.registry import get_manifest

app = FastAPI(
    title="beeOS M0 demo",
    description="beeBox (workload) + bee (runtime) — 无 Queen / 无 Hive",
    version="0.1.0",
)

# 共享 Bee 实例 + 审计日志（M0：单进程，配置写死）
AUDIT_PATH = "./logs/audit.jsonl"
_bee = Bee()
_audit = LocalAuditLog(AUDIT_PATH)


# === Schema ===

class RunRequest(BaseModel):
    """跑一个 Box 任务的请求体。"""

    box_type: str = Field(default="month_close", description="Box 类型（默认 month_close）")
    period: str = Field(default="2026-07", description="账期 YYYY-MM")
    approver: str = Field(default="manager@example.com", description="审批人邮箱")


# === API ===

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "beeos-m0", "version": "0.1.0"}


@app.get("/api/v0/boxes")
async def api_list_boxes() -> dict:
    return {"supported": list_supported()}


@app.get("/api/v0/boxes/{box_type}")
async def api_get_box(box_type: str) -> dict:
    try:
        return get_manifest(box_type)
    except ValueError:
        raise HTTPException(404, f"Unknown box: {box_type}. Supported: {list_supported()}")


@app.post("/api/v0/run")
async def api_run(req: RunRequest) -> dict:
    """同步跑一个 Box 任务（M0 单 Bee 串行，立即返回结果）。"""
    if req.box_type not in list_supported():
        raise HTTPException(400, f"Unknown box: {req.box_type}. Supported: {list_supported()}")
    result = await _bee.run(req.box_type, {"period": req.period, "approver": req.approver})
    return result.model_dump()


@app.get("/api/v0/audit")
async def api_audit(limit: int = 20) -> dict:
    """读最近 N 条审计（倒序）。"""
    path = Path(AUDIT_PATH)
    if not path.exists():
        return {"entries": []}
    lines = path.read_text().strip().split("\n")
    entries = []
    for line in reversed(lines[-limit:]):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"entries": entries}


# === 主页（M0 风格：BeeBox+Bee 中心化）===

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML


_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>beeOS M0 demo</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f9fafb; margin: 0; padding: 24px; color: #1f2937; }
    h1 { margin: 0 0 4px 0; }
    .sub { color: #6b7280; font-size: 14px; margin-bottom: 24px; }
    .row { display: grid; grid-template-columns: 1fr 60px 1fr; gap: 16px; margin-bottom: 24px; }
    .card { background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; }
    .card.bebox { border-color: #c4b5fd; }
    .card.bee { border-color: #86efac; }
    .arrow { display: flex; align-items: center; justify-content: center; color: #6b7280; font-size: 24px; }
    .arrow::before { content: "⇄"; }
    .label { font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; }
    .title { font-size: 18px; font-weight: bold; margin: 4px 0 12px 0; }
    .title.bebox { color: #6d28d9; }
    .title.bee { color: #16a34a; }
    .stat { display: flex; gap: 16px; font-size: 14px; color: #4b5563; margin-top: 8px; }
    .stat span { background: #f3f4f6; padding: 2px 8px; border-radius: 4px; }
    form { background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
    label { display: block; font-size: 12px; color: #6b7280; margin-bottom: 4px; }
    input, select { width: 100%; padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
    button { background: #2563eb; color: white; border: 0; padding: 10px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; margin-top: 12px; }
    button:hover { background: #1d4ed8; }
    button:disabled { background: #9ca3af; cursor: not-allowed; }
    pre { background: #1f2937; color: #e5e7eb; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 12px; max-height: 400px; }
    .audit { background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; margin-top: 16px; }
    .audit h3 { margin: 0 0 12px 0; }
    .audit-entry { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #f3f4f6; font-size: 13px; }
    .badge { background: #e0e7ff; color: #3730a3; padding: 2px 8px; border-radius: 4px; font-family: monospace; font-size: 11px; }
    .status { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }
    .status.done { background: #d1fae5; color: #065f46; }
    .status.failed { background: #fee2e2; color: #991b1b; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  </style>
</head>
<body>
  <h1>🐝 beeOS M0</h1>
  <div class="sub">beeBox (workload) × bee (runtime) · 无 Queen / 无 Hive · v0.1.0</div>

  <!-- BeeBox × Bee 主舞台 -->
  <div class="row">
    <div class="card bebox">
      <div class="label">🏢 工作空间 · workload</div>
      <div class="title bebox" id="box-name">MonthCloseBox</div>
      <div class="stat" id="box-stats">加载中…</div>
    </div>
    <div class="arrow"></div>
    <div class="card bee">
      <div class="label">👷 引擎 · runtime</div>
      <div class="title bee">1 bee 在岗</div>
      <div class="stat">
        <span>驱动：ReAct(M0: 静态)</span>
        <span>状态：<span id="bee-status">就绪</span></span>
      </div>
    </div>
  </div>

  <!-- 提交表单 -->
  <form id="run-form">
    <div class="grid-2">
      <div>
        <label>Box 类型</label>
        <select id="box_type">
          <option value="month_close">month_close（会计月结）</option>
        </select>
      </div>
      <div>
        <label>账期 (YYYY-MM)</label>
        <input type="text" id="period" value="2026-07">
      </div>
    </div>
    <div style="margin-top: 12px;">
      <label>审批人邮箱</label>
      <input type="email" id="approver" value="manager@example.com">
    </div>
    <button type="submit" id="run-btn">▶ 发起工单</button>
  </form>

  <!-- 结果区 -->
  <div id="result-container" style="display: none;">
    <div class="card" style="margin-bottom: 16px;">
      <h3 style="margin: 0 0 12px 0;">📋 执行结果 <span id="result-status" class="status done"></span></h3>
      <div style="font-size: 13px; color: #6b7280;" id="result-summary"></div>
    </div>
    <pre id="result-json"></pre>
  </div>

  <!-- 审计 -->
  <div class="audit">
    <h3>📜 最近审计 (本地 JSONL)</h3>
    <div id="audit-list">加载中…</div>
  </div>

<script>
async function loadBox() {
  const r = await fetch('/api/v0/boxes/month_close');
  const m = await r.json();
  document.getElementById('box-name').textContent = m.name;
  document.getElementById('box-stats').innerHTML =
    `<span>${m.tools.length} 工具</span><span>${m.workflow.length} 步骤</span><span>${m.schemas.length} schema</span>`;
}

async function loadAudit() {
  const r = await fetch('/api/v0/audit?limit=10');
  const d = await r.json();
  const html = d.entries.map(e => `
    <div class="audit-entry">
      <div>
        <span class="badge">${e.action}</span>
        <span style="color: #6b7280; margin-left: 8px;">${e.actor} · ${e.resource || '-'}</span>
      </div>
      <div style="color: #9ca3af; font-size: 11px;">${new Date(e.ts).toLocaleTimeString('zh-CN')}</div>
    </div>
  `).join('');
  document.getElementById('audit-list').innerHTML = html || '<div style="color: #9ca3af; text-align: center; padding: 16px;">暂无审计</div>';
}

document.getElementById('run-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('run-btn');
  btn.disabled = true;
  btn.textContent = '⏳ 执行中…';
  document.getElementById('bee-status').textContent = '忙';

  const body = {
    box_type: document.getElementById('box_type').value,
    period: document.getElementById('period').value,
    approver: document.getElementById('approver').value,
  };

  try {
    const r = await fetch('/api/v0/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await r.json();

    document.getElementById('result-container').style.display = 'block';
    document.getElementById('result-status').textContent = data.status;
    document.getElementById('result-status').className = 'status ' + (data.status === 'Done' ? 'done' : 'failed');
    document.getElementById('result-summary').textContent =
      `${data.steps.length} 步完成 · 耗时 ${data.elapsed_ms}ms · 账期 ${data.period}`;
    document.getElementById('result-json').textContent = JSON.stringify(data, null, 2);

    await loadAudit();
  } catch (err) {
    alert('执行失败: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ 发起工单';
    document.getElementById('bee-status').textContent = '就绪';
  }
});

loadBox();
loadAudit();
setInterval(loadAudit, 5000);
</script>
</body>
</html>"""
