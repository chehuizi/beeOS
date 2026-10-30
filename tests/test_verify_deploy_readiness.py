"""部署验证实例的**就绪检查**必须认得出「跑起来的是新构建」

**为什么这是条硬要求**（2026-10-30 实测撞出来的）：
那一轮链跑交付的新构建**启动就挂了**（Flyway 报
`relation "wms_zone" already exists`），
但就绪检查只看「端口通不通」——旧实例还没退干净，端口也是通的 →
**旧实例被当成了新构建就绪** → 测试盒对着**旧代码**跑 →
链报了「六盒全 accepted、落差 0」。

**这是这条链上最贵的一种假绿**：测试盒测的不是被测的那个。

所以就绪 = 三件事同时成立：
1. 端口通
2. **新进程自己还活着**（没启动就退出）
3. **日志里有「本次启动」的成功标记**（不是上一次启动留下的）
"""

from __future__ import annotations

import importlib.util
import pathlib
import socket
import subprocess
import sys
import time

import pytest


def _load_chain_module():
    spec = importlib.util.spec_from_file_location(
        "run_chain_mod",
        pathlib.Path(__file__).resolve().parent.parent / "scripts" / "run_chain.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _fake_java(tmp_path, body: str) -> str:
    """造一个假的 java：跑一段 python 脚本（stdout 会进日志）"""
    script = tmp_path / "fakejava.py"
    script.write_text(body, encoding="utf-8")
    wrapper = tmp_path / "fakejava"
    wrapper.write_text(
        f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n", encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return str(wrapper)


_HOLDS_PORT = """
import socket, sys, time
port = None
for a in sys.argv:
    if a.startswith("--server.port="):
        port = int(a.split("=", 1)[1])
print("Started WmsApplication in 0.1 seconds", flush=True)
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port))
s.listen(5)
time.sleep(120)
"""

_CRASHES = """
import sys
print("ERROR Application run failed", flush=True)
print("Message : ERROR: relation \\"wms_zone\\" already exists", flush=True)
sys.exit(1)
"""


@pytest.fixture()
def chain():
    return _load_chain_module()


def _deploy(chain, tmp_path, monkeypatch, port: int, java: str):
    monkeypatch.setenv(chain.VERIFY_JAVA_ENV, java)
    monkeypatch.setenv(chain.VERIFY_LOG_ENV, str(tmp_path / "verify.log"))
    monkeypatch.setenv(chain.VERIFY_RESET_DB_ENV, "0")
    monkeypatch.setenv(chain.VERIFY_DEPLOY_ENV, "1")
    jar = tmp_path / "wms-server-0.1.0.jar"
    jar.write_text("not a real jar", encoding="utf-8")
    return chain._deploy_to_verify_instance(
        {"artifact_path": str(jar)}, f"http://127.0.0.1:{port}/api/v1",
    )


class TestReadinessMeansTheNewBuild:
    def test_a_build_that_crashes_on_startup_aborts_the_chain(self, chain, tmp_path, monkeypatch):
        """启动就挂 → **中止**，不让测试盒去对着旧实例跑"""
        port = _free_port()
        java = _fake_java(tmp_path, _CRASHES)
        with pytest.raises(SystemExit) as exc:
            _deploy(chain, tmp_path, monkeypatch, port, java)
        assert "启动就退出" in str(exc.value)

    def test_an_old_instance_holding_the_port_is_not_taken_for_ready(
        self, chain, tmp_path, monkeypatch,
    ):
        """**这一条就是那轮假绿的守门人**

        旧实例占着端口（还没退干净），新构建启动就挂了 →
        只看「端口通不通」会把旧实例当成新构建就绪。
        """
        port = _free_port()
        # **旧实例**：占着端口，且**忽略 SIGTERM**（JVM 优雅关闭就是这样）
        old = subprocess.Popen(
            [
                sys.executable, "-c",
                "import signal,socket,time;"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                f"s=socket.socket();s.setsockopt(socket.SOL_SOCKET,"
                f"socket.SO_REUSEADDR,1);s.bind(('127.0.0.1',{port}));"
                "s.listen(5);time.sleep(120)",
            ],
        )
        try:
            java = _fake_java(tmp_path, _CRASHES)
            with pytest.raises(SystemExit):
                _deploy(chain, tmp_path, monkeypatch, port, java)
        finally:
            old.kill()
            old.wait(timeout=10)

    def test_a_healthy_start_is_accepted(self, chain, tmp_path, monkeypatch):
        port = _free_port()
        java = _fake_java(tmp_path, _HOLDS_PORT)
        note = _deploy(chain, tmp_path, monkeypatch, port, java)
        assert "已换成开发盒交付的构建" in note


class TestStartedMarkerComesFromThisRun:
    def test_a_marker_from_an_earlier_run_does_not_count(self, tmp_path):
        """上一次启动留下的成功标记，不能算这一次就绪"""
        chain = _load_chain_module()
        log = tmp_path / "verify.log"
        log.write_text(
            "Started WmsApplication in 9 seconds\n", encoding="utf-8",
        )
        offset = log.stat().st_size
        assert chain._started_ok(str(log), offset) is False

    def test_a_failure_marker_never_counts(self, tmp_path):
        chain = _load_chain_module()
        log = tmp_path / "verify.log"
        log.write_text(
            "Started WmsApplication in 9 seconds\n", encoding="utf-8",
        )
        offset = log.stat().st_size
        with log.open("a", encoding="utf-8") as fh:
            fh.write("Application run failed\n")
        assert chain._started_ok(str(log), offset) is False

    def test_a_marker_after_the_offset_counts(self, tmp_path):
        chain = _load_chain_module()
        log = tmp_path / "verify.log"
        log.write_text("old stuff\n", encoding="utf-8")
        offset = log.stat().st_size
        with log.open("a", encoding="utf-8") as fh:
            fh.write("Started WmsApplication in 6.9 seconds\n")
        assert chain._started_ok(str(log), offset) is True
