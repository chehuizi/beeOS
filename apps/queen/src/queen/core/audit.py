"""审计日志写入 - 哈希链式。

每条审计 log 含 prev_hash + curr_hash，curr_hash = SHA-256(prev_hash + JSON(payload))。
第一条 prev_hash = "0" * 64。

调用方：write_audit(actor, action, resource, payload) → 返回新 id。
"""

import hashlib
import json
from datetime import datetime

from sqlalchemy import select

from beeos_core.db import session_scope
from beeos_core.models import AuditLog

GENESIS_HASH = "0" * 64


def _compute_hash(prev_hash: str, payload: dict) -> str:
    """curr_hash = SHA-256(prev_hash + JSON(payload, sort_keys=True))。"""
    h = hashlib.sha256()
    h.update(prev_hash.encode())
    h.update(json.dumps(payload, sort_keys=True, default=str).encode())
    return h.hexdigest()


async def write_audit(actor: str, action: str, resource: str | None, payload: dict) -> int:
    """写一条审计日志，返回新 id。

    流程：
      1. 取最后一条 audit_log.curr_hash 作 prev_hash（无则 GENESIS_HASH）
      2. 构造 full_payload（含 ts，hash 涵盖时间）
      3. curr_hash = SHA-256(prev_hash + full_payload)
      4. INSERT AuditLog
    """
    async with session_scope() as session:
        stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(1)
        last = (await session.execute(stmt)).scalar_one_or_none()
        prev_hash = last.curr_hash if last else GENESIS_HASH

        ts = datetime.utcnow()
        full_payload = {
            "ts": ts.isoformat(),
            "actor": actor,
            "action": action,
            "resource": resource,
            "payload": payload,
        }
        curr_hash = _compute_hash(prev_hash, full_payload)

        entry = AuditLog(
            ts=ts,
            actor=actor,
            action=action,
            resource=resource,
            payload=payload,
            prev_hash=prev_hash,
            curr_hash=curr_hash,
        )
        session.add(entry)
        await session.flush()
        return entry.id
