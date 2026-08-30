"""本地审计日志（M0：写 JSONL，不入 PG）。

M0 实现：
- 每条审计 = 一行 JSON
- prev_hash 链式 hash（哈希链来自原 queen/core/audit.py，简化版）
- 默认写到 ./logs/audit.jsonl

V1+ 替换为 PG 哈希链（_shelved/queen/queen/core/audit.py）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GENESIS_HASH = "0" * 64


def _compute_hash(prev_hash: str, payload: dict) -> str:
    """curr_hash = SHA-256(prev_hash + JSON(payload, sort_keys=True))。"""
    h = hashlib.sha256()
    h.update(prev_hash.encode())
    h.update(json.dumps(payload, sort_keys=True, default=str).encode())
    return h.hexdigest()


class LocalAuditLog:
    """本地 JSONL 审计日志（append-only + 哈希链）。"""

    def __init__(self, path: str | Path = "./logs/audit.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash = self._load_last_hash()

    def _load_last_hash(self) -> str:
        """启动时读最后一行拿 curr_hash（保证重启后链不断）。"""
        if not self.path.exists():
            return GENESIS_HASH
        last_hash = GENESIS_HASH
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    last_hash = entry.get("curr_hash", GENESIS_HASH)
                except json.JSONDecodeError:
                    continue
        return last_hash

    def write(self, actor: str, action: str, resource: str | None, payload: dict) -> str:
        """写一条审计。返回 curr_hash。"""
        ts = datetime.now(timezone.utc)
        full = {
            "ts": ts.isoformat(),
            "actor": actor,
            "action": action,
            "resource": resource,
            "payload": payload,
        }
        prev_hash = self._last_hash
        curr_hash = _compute_hash(prev_hash, full)

        entry = {
            "ts": full["ts"],
            "actor": actor,
            "action": action,
            "resource": resource,
            "payload": payload,
            "prev_hash": prev_hash,
            "curr_hash": curr_hash,
        }
        with self.path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._last_hash = curr_hash
        return curr_hash
