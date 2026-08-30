"""M0 demo FastAPI server（无 PG / 无持久化）。

路由：
  GET  /                     BeeBox+Bee 中心化的 demo 页
  GET  /api/v0/boxes         列出已注册 Box
  GET  /api/v0/boxes/{type}  取 Box manifest
  POST /api/v0/run           跑一个 Box 任务（同步返回）
  GET  /api/v0/stats         bee runtime 累计统计（运行次数 / 成功率 / 平均耗时）
  GET  /api/v0/audit         读最近 N 条审计（开发者调试用，不暴露在 demo 页）
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
    title="Beeline · M0 demo",
    description="Beeline — 让 AI 数字员工飞直线的运行时 (agentbeeline.com) · M0 数字员工 demo",
    version="0.1.0",
)

# 共享 Bee 实例 + 审计日志（M0：单进程，配置写死）
AUDIT_PATH = "./logs/audit.jsonl"
_bee = Bee()
_audit = LocalAuditLog(AUDIT_PATH)

# 累计运行统计（M0 内存版）
_run_stats = {"total": 0, "success": 0, "total_ms": 0.0}


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
        manifest = dict(get_manifest(box_type))  # 复制
        # 补 workflow 字段长度（manifest 本身没存，但前端的 stats 展示需要）
        from bee.registry import get_workflow
        manifest["workflow_count"] = len(get_workflow(box_type))
        return manifest
    except ValueError:
        raise HTTPException(404, f"Unknown box: {box_type}. Supported: {list_supported()}")


@app.post("/api/v0/run")
async def api_run(req: RunRequest) -> dict:
    """同步跑一个 Box 任务（M0 单 Bee 串行，立即返回结果）。"""
    if req.box_type not in list_supported():
        raise HTTPException(400, f"Unknown box: {req.box_type}. Supported: {list_supported()}")
    _run_stats["total"] += 1
    result = await _bee.run(req.box_type, {"period": req.period, "approver": req.approver})
    _run_stats["total_ms"] += result.elapsed_ms
    if result.status.value == "Done":
        _run_stats["success"] += 1
    return result.model_dump()


@app.get("/api/v0/stats")
async def api_stats() -> dict:
    """bee runtime 累计统计。"""
    total = _run_stats["total"]
    success = _run_stats["success"]
    return {
        "total_runs": total,
        "success_rate": round(success / total, 3) if total > 0 else None,
        "avg_ms": round(_run_stats["total_ms"] / total, 2) if total > 0 else None,
    }


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
  <title>Beeline · M0 demo · agentbeeline.com</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f9fafb; margin: 0; padding: 24px; color: #1f2937; }
    h1 { margin: 0 0 4px 0; }
    .sub { color: #6b7280; font-size: 14px; margin-bottom: 24px; }
    .row { display: grid; grid-template-columns: 1fr 60px 1fr; gap: 16px; margin-bottom: 24px; }
    .card { background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; }
    .card.bebox { border-color: #c4b5fd; }
    .card.bee { border-color: #86efac; }
    .arrow { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; }
    .arrow svg { display: block; }
    .flow-dash {
      stroke-dasharray: 5 4;
      animation: flow-anim 1.2s linear infinite;
    }
    .arrow.busy .flow-dash { animation-duration: 0.4s; }
    @keyframes flow-anim {
      to { stroke-dashoffset: -18; }
    }
    .flow-label {
      font-size: 10px; color: #6b7280; font-family: monospace;
      letter-spacing: 0.3px; text-align: center; white-space: nowrap;
    }
    .label { font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; }
    .title { font-size: 18px; font-weight: bold; margin: 4px 0 2px 0; }
    .title.bebox { color: #6d28d9; }
    .title.bee { color: #16a34a; }
    .subtitle { font-size: 13px; color: #6b7280; margin-bottom: 14px; }
    .stat { display: flex; gap: 16px; font-size: 14px; color: #4b5563; margin-top: 8px; }
    .stat span { background: #f3f4f6; padding: 2px 8px; border-radius: 4px; }

    .section {
      font-size: 11px; color: #6b7280; text-transform: uppercase;
      letter-spacing: 0.5px; margin: 16px 0 6px 0; font-weight: 600;
    }
    .step-list { list-style: none; padding: 0; margin: 0 0 4px 0; font-size: 13px; }
    .step-list li { display: flex; align-items: center; gap: 8px; padding: 3px 0; color: #374151; }
    .step-num {
      display: inline-block; width: 18px; height: 18px;
      background: #ede9fe; color: #6d28d9; border-radius: 4px;
      font-size: 11px; font-weight: 600; text-align: center; line-height: 18px;
    }
    .tag-list { display: flex; flex-wrap: wrap; gap: 4px; }
    .tag {
      background: #f3f4f6; color: #4b5563; font-size: 11px;
      padding: 2px 6px; border-radius: 3px; font-family: monospace;
    }
    .cap-list { list-style: none; padding: 0; margin: 0 0 4px 0; font-size: 13px; color: #374151; }
    .cap-list li { padding: 2px 0; }
    .cap-list li::before { content: "· "; color: #9ca3af; }
    .dim { color: #9ca3af; font-size: 12px; }
    .status-dot {
      display: inline-block; width: 8px; height: 8px;
      background: #16a34a; border-radius: 50%; margin-right: 4px;
      box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.15);
    }
    .status-dot.busy { background: #f59e0b; box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.15); }
    .stat-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .stat-cell {
      background: #f9fafb; border: 1px solid #e5e7eb;
      border-radius: 6px; padding: 8px 4px; text-align: center;
    }
    .stat-cell .num { font-size: 20px; font-weight: bold; color: #1f2937; line-height: 1.2; }
    .stat-cell .cap-label { font-size: 10px; color: #6b7280; margin-top: 2px; }
    form { background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
    label { display: block; font-size: 12px; color: #6b7280; margin-bottom: 4px; }
    input, select { width: 100%; padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
    button { background: #2563eb; color: white; border: 0; padding: 10px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; margin-top: 12px; }
    button:hover { background: #1d4ed8; }
    button:disabled { background: #9ca3af; cursor: not-allowed; }
    pre { background: #1f2937; color: #e5e7eb; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 12px; max-height: 400px; }
    .status { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }
    .status.done { background: #d1fae5; color: #065f46; }
    .status.failed { background: #fee2e2; color: #991b1b; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

    /* === 蜜蜂动画 === */
    @keyframes bee-bob {
      0%, 100% { transform: translateY(0); }
      50%      { transform: translateY(-2px); }
    }
    @keyframes wing-flap {
      0%, 100% { transform: scaleY(1); }
      50%      { transform: scaleY(0.4); }
    }
    @keyframes bee-blink {
      0%, 88%, 100% { transform: scaleY(1); }
      92%, 96%      { transform: scaleY(0.1); }
    }
    .bee-bob {
      animation: bee-bob 3s ease-in-out infinite;
      transform-origin: 18px 22px;
    }
    .bee-wing {
      transform-box: fill-box;
      animation: wing-flap 0.5s ease-in-out infinite;
    }
    .bee-wing-left  { transform-origin: 100% 50%; }
    .bee-wing-right { transform-origin: 0% 50%; }
    .bee-eyes {
      transform-box: fill-box;
      transform-origin: 50% 50%;
      animation: bee-blink 4.5s ease-in-out infinite;
    }
  </style>
</head>
<body>
  <h1 style="display:flex;align-items:center;gap:12px;">
    <svg width="36" height="36" viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="bee">
      <g class="bee-bob">
        <!-- 左翅（拍动） -->
        <g class="bee-wing bee-wing-left">
          <ellipse cx="11" cy="16" rx="6" ry="5" fill="#fff" opacity="0.7"/>
          <ellipse cx="11" cy="16" rx="6" ry="5" fill="none" stroke="#cbd5e1" stroke-width="0.6"/>
        </g>
        <!-- 右翅（拍动） -->
        <g class="bee-wing bee-wing-right">
          <ellipse cx="25" cy="16" rx="6" ry="5" fill="#fff" opacity="0.7"/>
          <ellipse cx="25" cy="16" rx="6" ry="5" fill="none" stroke="#cbd5e1" stroke-width="0.6"/>
        </g>
        <!-- 身体（圆胖椭圆） -->
        <ellipse cx="18" cy="22" rx="9" ry="7.5" fill="#fbbf24"/>
        <!-- 条纹（圆角矩形） -->
        <rect x="9.5" y="20" width="17" height="2" rx="1" fill="#374151"/>
        <rect x="9.5" y="24" width="17" height="2" rx="1" fill="#374151"/>
        <!-- 头（圆） -->
        <circle cx="18" cy="13" r="6" fill="#fbbf24"/>
        <!-- 腮红（粉圆） -->
        <circle cx="13" cy="14.5" r="1.3" fill="#fb7185" opacity="0.5"/>
        <circle cx="23" cy="14.5" r="1.3" fill="#fb7185" opacity="0.5"/>
        <!-- 眼睛（眨） -->
        <g class="bee-eyes">
          <circle cx="15.5" cy="12.5" r="1.1" fill="#1f2937"/>
          <circle cx="20.5" cy="12.5" r="1.1" fill="#1f2937"/>
          <circle cx="16" cy="12" r="0.4" fill="#fff"/>
          <circle cx="21" cy="12" r="0.4" fill="#fff"/>
        </g>
        <!-- 微笑 -->
        <path d="M16 15 Q18 16.3 20 15" stroke="#1f2937" stroke-width="0.9" fill="none" stroke-linecap="round"/>
        <!-- 触角 -->
        <line x1="15.5" y1="8" x2="14" y2="5" stroke="#1f2937" stroke-width="0.9" stroke-linecap="round"/>
        <line x1="20.5" y1="8" x2="22" y2="5" stroke="#1f2937" stroke-width="0.9" stroke-linecap="round"/>
        <circle cx="14" cy="4.5" r="1" fill="#1f2937"/>
        <circle cx="22" cy="4.5" r="1" fill="#1f2937"/>
      </g>
    </svg>
    <span style="display:flex;align-items:baseline;gap:10px;">
      Beeline
      <a href="https://agentbeeline.com" target="_blank" rel="noopener"
         style="font-size:13px;font-weight:normal;color:#2563eb;text-decoration:none;border:1px solid #bfdbfe;padding:2px 8px;border-radius:999px;background:#eff6ff;">
        agentbeeline.com ↗
      </a>
    </span>
  </h1>
  <div class="sub">beeBox (workload) × bee (runtime) · M0 数字员工运行时 · v0.1.0</div>

  <!-- BeeBox × Bee 主舞台 -->
  <div class="row">
    <div class="card bebox">
      <div class="label">🏢 工作空间 · workload</div>
      <div class="title bebox" id="box-name">MonthCloseBox</div>
      <div class="subtitle" id="box-subtitle">v0.1.0 · 会计月结自动化</div>

      <div class="section">📋 6 步 workflow</div>
      <ol class="step-list" id="box-steps">
        <li><span class="step-num">1</span> 拉余额（应付+应收）</li>
        <li><span class="step-num">2</span> 银行对账</li>
        <li><span class="step-num">3</span> 费用分类</li>
        <li><span class="step-num">4</span> 生成三大报表</li>
        <li><span class="step-num">5</span> 凭证归集</li>
        <li><span class="step-num">6</span> 发起审批</li>
      </ol>

      <div class="section">🔧 7 个工具 · 7 个 schema</div>
      <div class="tag-list" id="box-tags">
        <span class="tag">accounts_payable</span>
        <span class="tag">accounts_receivable</span>
        <span class="tag">bank_reconcile</span>
        <span class="tag">expense_classify</span>
        <span class="tag">report_generate</span>
        <span class="tag">evidence_collect</span>
        <span class="tag">signoff_request</span>
      </div>
    </div>
    <div class="arrow" id="flow-arrow">
      <svg width="64" height="14" viewBox="0 0 64 14">
        <line x1="2" y1="7" x2="56" y2="7" stroke="url(#g)" stroke-width="2"
              stroke-linecap="round" class="flow-dash"/>
        <polygon points="52,3 60,7 52,11" fill="#16a34a"/>
        <defs>
          <linearGradient id="g" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="#a78bfa"/>
            <stop offset="100%" stop-color="#86efac"/>
          </linearGradient>
        </defs>
      </svg>
      <div class="flow-label">workflow → execution</div>
    </div>
    <div class="card bee">
      <div class="label">👷 引擎 · runtime</div>
      <div class="title bee">1 bee</div>
      <div class="subtitle">
        <span class="status-dot"></span> <span id="bee-status">就绪 · 待命</span>
      </div>

      <div class="section">⚙️ 能力</div>
      <ul class="cap-list">
        <li>状态机 · 5 态</li>
        <li>异常捕获 + 重试</li>
        <li>本地 JSONL 审计</li>
        <li>ReAct 循环 <span class="dim">（V1+）</span></li>
      </ul>

      <div class="section">📊 累计</div>
      <div class="stat-row">
        <div class="stat-cell">
          <div class="num" id="total-runs">0</div>
          <div class="cap-label">运行次数</div>
        </div>
        <div class="stat-cell">
          <div class="num" id="success-rate">—</div>
          <div class="cap-label">成功率</div>
        </div>
        <div class="stat-cell">
          <div class="num" id="avg-ms">—</div>
          <div class="cap-label">平均耗时</div>
        </div>
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

<script>
async function loadStats() {
  const r = await fetch('/api/v0/stats');
  const s = await r.json();
  document.getElementById('total-runs').textContent = s.total_runs;
  document.getElementById('success-rate').textContent =
    s.success_rate === null ? '—' : (s.success_rate * 100).toFixed(0) + '%';
  document.getElementById('avg-ms').textContent =
    s.avg_ms === null ? '—' : s.avg_ms.toFixed(1) + 'ms';
}

document.getElementById('run-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('run-btn');
  btn.disabled = true;
  btn.textContent = '⏳ 执行中…';
  const statusEl = document.getElementById('bee-status');
  const dotEl = document.querySelector('.status-dot');
  const arrowEl = document.getElementById('flow-arrow');
  statusEl.textContent = '执行中…';
  if (dotEl) dotEl.classList.add('busy');
  if (arrowEl) arrowEl.classList.add('busy');

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
    await loadStats();

    document.getElementById('result-container').style.display = 'block';
    document.getElementById('result-status').textContent = data.status;
    document.getElementById('result-status').className = 'status ' + (data.status === 'Done' ? 'done' : 'failed');
    document.getElementById('result-summary').textContent =
      `${data.steps.length} 步完成 · 耗时 ${data.elapsed_ms}ms · 账期 ${data.period}`;
    document.getElementById('result-json').textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    alert('执行失败: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ 发起工单';
    statusEl.textContent = '就绪 · 待命';
    if (dotEl) dotEl.classList.remove('busy');
    if (arrowEl) arrowEl.classList.remove('busy');
  }
});

loadStats();
</script>
</body>
</html>"""
