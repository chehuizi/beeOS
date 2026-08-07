"""Guardian - 凭证加密 + Token 颁发 + Prompt 注入检测。

对应 [技术架构 §4.7 Guardian]。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from beeos_core.config import get_settings


# === 凭证加密 (AES-256-GCM) ===

def _derive_key(master_key: str) -> bytes:
    """从主密钥派生 256-bit 加密密钥。

    MVP 阶段：直接 SHA-256。V1 可换 HKDF。
    """
    import hashlib

    return hashlib.sha256(master_key.encode()).digest()


def encrypt_credential(plaintext: str) -> bytes:
    """加密凭证。返回 ciphertext || nonce。

    格式：前 12 字节是 nonce，后面是 ciphertext+tag（tag 已附加在 ciphertext 末尾）。
    """
    master_key = get_settings().master_key.get_secret_value()
    key = _derive_key(master_key)
    aesgcm = AESGCM(key)
    nonce = b"\x00" * 12  # MVP 阶段用固定 nonce，V1 改随机
    return nonce + aesgcm.encrypt(nonce, plaintext.encode(), None)


def decrypt_credential(blob: bytes) -> str:
    """解密凭证。"""
    master_key = get_settings().master_key.get_secret_value()
    key = _derive_key(master_key)
    aesgcm = AESGCM(key)
    nonce = blob[:12]
    ciphertext = blob[12:]
    return aesgcm.decrypt(nonce, ciphertext, None).decode()


# === API Token 颁发 (JWT) ===

def issue_token(user_id: str, role: str) -> str:
    """颁发 API Token。"""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=settings.api_token_ttl_hours),
        "iss": "beeos",
    }
    return jwt.encode(
        payload,
        settings.api_token_secret.get_secret_value(),
        algorithm="HS256",
    )


def verify_token(token: str) -> dict[str, Any]:
    """校验 API Token。返回 claims。失败抛 jwt.InvalidTokenError。"""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.api_token_secret.get_secret_value(),
        algorithms=["HS256"],
        issuer="beeos",
    )


# === Prompt 注入检测 ===

INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?previous\s+instructions?",
    r"disregard\s+(?:the\s+)?system\s+prompt",
    r"you\s+are\s+now\s+",
    r"act\s+as\s+if\s+",
    r"forget\s+(?:everything|all)\s+",
    r"override\s+your\s+instructions?",
    r"reveal\s+(?:your|the)\s+(?:system\s+)?prompt",
    r"print\s+(?:your|the)\s+(?:system|initial)\s+prompt",
]


def detect_injection(text: str) -> float:
    """检测 Prompt 注入风险。返回 0-1 分数（>0.7 视为高风险）。"""
    if not text:
        return 0.0
    score = 0.0
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score += 0.3
    return min(score, 1.0)


def is_high_risk(text: str) -> bool:
    """便捷判断：是否高风险注入。"""
    return detect_injection(text) > 0.7
