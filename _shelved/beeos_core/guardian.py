"""Guardian - AES-256-GCM + JWT + Prompt 注入检测。

⚠️  M0 注意事项：原代码 encrypt_credential() 用了固定 nonce（`b"\\x00" * 12`），
   这是 AES-GCM 的严重安全 bug。V1 恢复前必须先修。

详见 _shelved/README.md。
"""
