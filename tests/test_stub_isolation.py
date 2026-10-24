"""tests.test_stub_isolation - 测试桩不许漏到别的测试文件

**为什么这条要单独守**：直接给 `llm.complete_json` 赋值，装桩的测试一结束，
桩就留在原地了。下一个测试文件以为自己调的是真 LLM，其实调的是上一个
文件留下的桩——失败原因离现场隔着两个文件，而且**只在全量跑的时候出现**
（单跑那个文件是绿的）。

实测踩过：新增的抽取层守卫文件直接赋值了 `llm.complete_json`，
全量跑时把后面 5 条 LLM 重试用例全炸了；单跑那 5 条是绿的。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kanban.trigger import trigger_task
from runtime.store import TaskRunStore

TESTS_DIR = Path(__file__).resolve().parent


class TestTheStubDoesNotEscape:
    def test_捕获盒的桩用完就还原(self):
        """跑完一次带桩的捕获，桩必须已经还原

        这条守的是**捕获盒那个 helper**——它以前是直接赋值，
        桩就留在了原地，后面 5 条 LLM 重试用例全被炸掉。
        """
        from runtime import llm
        from tests.test_capture_extraction import _run_capture

        original = llm.complete_json
        store = TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))
        _run_capture(
            store, "建模退款流程。",
            llm_result={"nodes": [
                {"action": "建模退款流程", "trace_to": "sent_1"},
            ]},
        )
        assert llm.complete_json is original

    def test_仓库里没有直接给_llm_桩赋值(self):
        """桩只许用 monkeypatch.setattr 装

        直接赋值（`llm.complete_json = ...`）会漏到下一个测试文件。
        """
        offenders: list[str] = []
        for path in sorted(TESTS_DIR.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if not stripped.startswith(("llm.complete_json =", "llm.complete =")):
                    continue
                # 允许出现在「字符串里」（比如守卫自己举的例子）
                if stripped.startswith(('"', "'")):
                    continue
                offenders.append(f"{path.name}:{lineno}")
        assert not offenders, (
            "这些地方直接赋值了 LLM 桩，会漏到下一个测试文件：\n"
            + "\n".join(offenders)
        )
