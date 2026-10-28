"""
runtime.store - TaskRun JSONL 持久化 + 查询

设计要点：
- JSONL 追加写（每行一个 task_run 序列化记录）
- 启动时从文件加载全部历史记录（in-memory cache）
- 写时同步落盘（保证持久性）
- 查询接口：list_recent / count_by_status / list_exceptions / aggregate_metrics

Record 形状（精简版，比完整 TaskRun 小）：
  task_run_id, box_id, beeline_id, status, acceptance_status,
  started_at, finished_at, op_count, exception_count, duration_ms,
  observed（本次实测到的判据原值）, error_kind（中止归因）,
  artifact（**产出本体**）, artifact_truncated（产出是否因超限被截断）

为什么要 observed：盒子在 definition.metrics 里声明了指标目标，
但目标不是事实。事实得从每次履约的实测值里聚合出来，而实测值原先只活在
当次请求的响应体里——请求一关就没了，声明就永远只是声明。
落盘这一份，盒子才能在任意时刻回答「我最近跑得怎么样」。

为什么要 artifact：产出以前也只活在当次响应体里。理由写的是「体积大且
能从重放得到」，但**「能重放得到」对 LLM 腿是错的**——那两只盒子都调
complete_json 带采样，重放出来的是另一个产出。落盘这一份，产出才可引用、
可对账、可追溯。体积大的那半仍然成立，所以设 ARTIFACT_MAX_BYTES 上限，
超了**显式标 truncated**，不静默丢。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from runtime.models import TaskRun, TaskRunStatus, AcceptanceStatus

# 单条产出落盘上限。nodes / edges 的图能到几百 KB，而 task run 记录是
# JSONL 追加写、每次启动全量载入内存——不设上限的话看板会随产出图变大
# 一起变慢。超限不是静默丢，是 artifact_truncated=true + 一份说明。
ARTIFACT_MAX_BYTES = 256 * 1024


def task_run_to_record(
    task_run: TaskRun,
    box_id: str,
    acceptance_status: Optional[str] = None,
    rejection_class: Optional[str] = None,
    observed: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """TaskRun → 精简持久化 record"""
    duration_ms = 0
    if task_run.identity.started_at and task_run.identity.finished_at:
        duration_ms = int((task_run.identity.finished_at - task_run.identity.started_at).total_seconds() * 1000)

    exception_count = sum(
        1 for e in task_run.event_log.entries
        if e.event == "op_finish" and (e.detail or {}).get("status") == "failed"
    )

    # 规则介入记录：哪些环节没走模型必须事后可查。
    # 原字段叫 degradation —— 降级删掉后它已经不指降级了，改名免得误导。
    # 现在的两种情况：捕获盒恒为 None（只有 LLM 一条腿）；
    # 建模盒可能带 note（business_goal 取规则版、id 重编号）。
    rule_applied = None
    if task_run.result:
        note = task_run.result.get("_extractor_note") or ""
        if note:
            rule_applied = {"note": note}

    # **产出本体落盘**（以前不落盘）。
    #
    # 当初不落的理由是「体积大且能从重放得到」——**后半句对 LLM 腿是错的**：
    # receipt_capture_box / requirement_capture_box 都调 complete_json 带采样，
    # 重放拿到的是**另一个**产出，不是这一个。也就是说不落盘 = 产出永久丢失，
    # 只有 trigger 那一瞬间的 HTTP 响应里存在过。
    #
    # 「体积大」那半句仍然成立（nodes / edges 的图），所以这里设上限：
    # 超了**显式截断并标 artifact_truncated=true**，不做静默丢弃——
    # 静默丢字段和「算了却看不见」是同一族病，读的人无从知道自己拿到的是全的。
    artifact = None
    artifact_truncated = False
    if task_run.result is not None:
        encoded = json.dumps(task_run.result, ensure_ascii=False, default=str)
        if len(encoded) <= ARTIFACT_MAX_BYTES:
            artifact = task_run.result
        else:
            artifact_truncated = True
            artifact = {
                "_truncated": True,
                "_full_bytes": len(encoded),
                "_note": (
                    f"产出 {len(encoded)} 字节，超过上限 {ARTIFACT_MAX_BYTES}，"
                    "这里只保留了元信息——不是产出本身"
                ),
            }

    # 中止归因：整轮跑挂在哪个设施上（llm_call / op_error / 没挂）。
    error_kind = None
    error_reason = ""
    for op in task_run.operations.values():
        if op.status == "failed":
            error_kind = op.error_kind or "op_error"
            error_reason = op.error or ""
            break
    if not error_reason:
        # op 没留下错误，但状态机记了转移原因（例：起点 op 缺失、步数超限）——
        # 那是同样重要的失败信息，不能因为「没有 op 错误」就丢。
        for event in reversed(task_run.event_log.entries):
            if event.event == "status_change":
                error_reason = str((event.detail or {}).get("reason") or "")
                break

    return {
        "task_run_id": task_run.identity.task_run_id,
        "box_id": box_id,
        "beeline_id": task_run.identity.beeline_id,
        "beeline_version": task_run.identity.beeline_version,
        "intent": task_run.identity.intent,
        "originator": task_run.identity.originator,
        # 每个 op 的执行轨迹（按执行顺序）。
        #
        # **以前不落盘**：op_trace 只活在 trigger 那一瞬间的 HTTP 响应里，
        # 请求一关就没了——于是「这只盒子刚才跑了哪几步、哪一步挂了、
        # 挂在什么设施上」这件事，刷新一下页面就再也问不出来。
        # 跟产出本体不落盘是同一族病（那边已有注释说明为什么必须落盘）。
        #
        # 只存**事实**（op_id / 状态 / 错误 / 时间），不存 output——
        # output 的体积在产出本体里已经有了，这里再存一份就是抄两份。
        "op_trace": [
            {
                "op_id": rec.op_id,
                "status": rec.status,
                "error": rec.error or "",
                "error_kind": rec.error_kind or "",
                "started_at": rec.started_at.isoformat() if rec.started_at else None,
                "finished_at": rec.finished_at.isoformat() if rec.finished_at else None,
            }
            for rec in task_run.operations.values()
        ],
        # 业务对象标识：归档按它把履约归到工作单元（需求 / 运维任务）。
        # 索引字段——它在 payload 里刨不出来，所以必须是记录的一等公民。
        "subject_ref": task_run.identity.subject_ref,
        # 业务对象的种类：只有 ref 不够——需求号和运维事件号可能撞号，
        # 撞了分不出「这条需求的一次运维」和「一个运维事件」。
        "subject_kind": task_run.identity.subject_kind,
        "status": task_run.status.value,
        "acceptance_status": acceptance_status,
        "rejection_class": rejection_class,
        "rule_applied": rule_applied,
        "artifact": artifact,
        "artifact_truncated": artifact_truncated,
        "observed": observed or {},
        "error_kind": error_kind,
        "error_reason": error_reason,
        "started_at": task_run.identity.started_at.isoformat() if task_run.identity.started_at else None,
        "finished_at": task_run.identity.finished_at.isoformat() if task_run.identity.finished_at else None,
        "created_at": task_run.identity.created_at.isoformat(),
        "op_count": len(task_run.operations),
        "exception_count": exception_count,
        "duration_ms": duration_ms,
    }


class TaskRunStore:
    """TaskRun JSONL 持久化 + 查询

    用法：
        store = TaskRunStore(path="logs/task_runs.jsonl")
        store.append(task_run, box_id="wms_design_box", acceptance_status="accepted")
        recent = store.list_recent(limit=10)
    """

    def __init__(self, path: str | Path = "logs/task_runs.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        """启动时从文件加载"""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._cache.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    def append(
        self,
        task_run: TaskRun,
        box_id: str,
        acceptance_status: Optional[str] = None,
        rejection_class: Optional[str] = None,
        observed: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """追加一条 task run 记录（同步落盘）"""
        record = task_run_to_record(
            task_run, box_id, acceptance_status, rejection_class, observed
        )
        self._cache.append(record)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def list_recent(self, limit: int = 20, box_id: Optional[str] = None) -> list[dict[str, Any]]:
        """最近的 task run（倒序）"""
        records = self._cache
        if box_id is not None:
            records = [r for r in records if r["box_id"] == box_id]
        # 按 created_at 倒序
        return sorted(records, key=lambda r: r["created_at"], reverse=True)[:limit]

    def get_run(self, task_run_id: str) -> Optional[dict[str, Any]]:
        """按 task_run_id 取单次履约的完整记录（含产出本体）

        找不到返回 None —— 调用方据此回 404。
        这是「引用一次产出」的最小单位：拿到 run_id 就能把当时的产出、
        判据实测值、中止归因一并取回来，不必重放（LLM 腿重放也不等于原来那次）。
        """
        for r in self._cache:
            if r.get("task_run_id") == task_run_id:
                return r
        return None

    def list_by_subject(
        self,
        subject_ref: str,
        limit: int = 200,
        *,
        box_id: Optional[str] = None,
        accepted_only: bool = False,
        subject_kind: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """某个业务对象关联的所有履约（倒序）

        归档按「工作单元」分组变化时用这个：一个需求集（set_id），
        一路串起设计、开发、测试、分析那几次履约。

        `accepted_only=True` 是归档写回的门槛：**只有验过的才进档案**。
        不验就写回去，等于把「说过的东西」当成「事实」记下来——
        档案是下一个需求的地面，地面上的错会一路传下去。

        `subject_kind` 用来把「同号不同类」分开：ops 盒子的运维事件号
        和其它盒子的需求号可能撞上，只按 ref 过滤会把两类记录混在一起。
        不传 = 不按种类过滤（老记录没有这个字段时也走这条路）。
        """
        records = [
            r for r in self._cache
            if r.get("subject_ref") == subject_ref
        ]
        if subject_kind is not None:
            records = [r for r in records if r.get("subject_kind") == subject_kind]
        if box_id is not None:
            records = [r for r in records if r["box_id"] == box_id]
        if accepted_only:
            records = [r for r in records if r.get("acceptance_status") == "accepted"]
        return sorted(records, key=lambda r: r["created_at"], reverse=True)[:limit]

    def list_artifacts(
        self,
        box_id: str,
        limit: int = 20,
        *,
        accepted_only: bool = False,
    ) -> list[dict[str, Any]]:
        """某只盒子产出过的产物（倒序）

        **只收录真正产出东西的那次**：failed 的轮次没有产出，accepted/rejected
        才有产出可读——rejected 的产出照样能读，判它不通过和读它产出了什么
        是两件事，要看「为什么被判红」就不能把 rejected 过滤掉。

        形状是「产出 + 出自哪一次」两层：产出本体给人/程序用，
        run_id / 时间戳 / 判据结论给它可追溯性。没有后面那层，
        一份孤立的产出没法回答「这是什么时候、哪一次跑出来的」。
        """
        rows = [
            r
            for r in self._cache
            if r["box_id"] == box_id and r.get("artifact") is not None
            and (not accepted_only or r.get("acceptance_status") == "accepted")
        ]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]

    def count_by_status(self) -> dict[str, int]:
        """按 status 统计"""
        return dict(Counter(r["status"] for r in self._cache))

    def count_by_acceptance(self) -> dict[str, int]:
        """按 acceptance status 统计"""
        return dict(Counter(
            r["acceptance_status"] or "pending"
            for r in self._cache
        ))

    def list_exceptions(self, limit: int = 10) -> list[dict[str, Any]]:
        """含异常的 task run"""
        return sorted(
            [r for r in self._cache if r["exception_count"] > 0 or r["status"] == "failed"],
            key=lambda r: r["created_at"],
            reverse=True,
        )[:limit]

    def aggregate_metrics(self, box_id: Optional[str] = None) -> dict[str, Any]:
        """聚合统计

        Args:
            box_id: 可选，按 box_id 过滤
        """
        records = self._cache
        if box_id is not None:
            records = [r for r in records if r["box_id"] == box_id]

        total = len(records)
        if total == 0:
            return {"total": 0}

        completed = [r for r in records if r["status"] == "completed"]
        failed = [r for r in records if r["status"] == "failed"]
        accepted = [r for r in records if r["acceptance_status"] == "accepted"]
        rejected = [r for r in records if r["acceptance_status"] == "rejected"]

        durations = [r["duration_ms"] for r in completed if r["duration_ms"] > 0]
        avg_duration = sum(durations) / len(durations) if durations else 0

        return {
            "total": total,
            "completed": len(completed),
            "failed": len(failed),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "acceptance_rate": f"{len(accepted) / total * 100:.1f}%" if total > 0 else "0.0%",
            "failure_rate": f"{len(failed) / total * 100:.1f}%" if total > 0 else "0.0%",
            "avg_duration_ms": int(avg_duration),
        }

    def list_boxes(self) -> list[str]:
        """所有出现过的 box_id"""
        return sorted({r["box_id"] for r in self._cache})

    def view(self, box_ids: set[str]) -> "TaskRunStore":
        """只含指定 box 记录的内存视图（不写文件、不影响原 store）

        用于看板只展示已注册盒子——未注册盒子的历史记录保留在 JSONL
        审计日志里，只是不显示。
        """
        v = TaskRunStore.__new__(TaskRunStore)
        v.path = self.path
        v._cache = [r for r in self._cache if r["box_id"] in box_ids]
        return v