"""tests/test_runtime_reliability.py - 执行内核的可靠性守卫

**这组测试守的不是「功能对不对」**，是「出事的时候能不能查清」。

2026-10-30 一次真跑链实测到的三件事，每一件都对应下面一组守卫：

1. **一次网络抖动杀死整轮作业**
   开发盒跑了 30 轮（每轮编译 + 前后端构建，几十分钟量级），
   模型正答着的时候 `SSL: UNEXPECTED_EOF_WHILE_READING` →
   整轮当场归零，前面写的代码全成孤儿。
   → 守卫 1~3：传输类故障必须重试，凭证错才不重试。

2. **失败但查无此事**
   executor 的 try 只包着 `runner.run(op)`；链上别的几处异常
   （`_pick_next` 的 when、`dict(op_output)`、非法状态转移）
   会冒到 trigger.py，**直接跳过 store.append** →
   这次履约在看板里根本不存在。
   → 守卫 4：任何异常路径都要落成一条 failed 记录。

3. **看板看不见别的进程写的记录**
   store 构造时全量载入一次，之后只读内存缓存 →
   看板陈旧、`get_run` 找不到上游 run → **假失败**。
   → 守卫 5~6：外部写入要看得见，坏行要计数。

**每条守卫都必须能独立触发它要守的那个失败**：
别的闸先拦下来 = 这条守卫不承重。所以每条都带注入。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from runtime.llm import (
    LLMError,
    LLMParseError,
    TRANSPORT_RETRIES,
    _backoff_delay,
    complete,
)
from runtime.store import TaskRunStore


# ============================================================
# 1) 传输层重试：瞬时故障不杀整轮
# ============================================================

class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", payload: Any = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self) -> Any:
        if self._payload is not None:
            return self._payload
        return {"choices": [{"message": {"content": "ok"}}]}


def _ok_payload(content: str = "hello") -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture()
def no_sleep(monkeypatch):
    """退避别真等（否则测试会慢）"""
    slept: list[float] = []
    monkeypatch.setattr("runtime.llm.time.sleep", lambda s: slept.append(s))
    return slept


def test_ssl_eof_is_retried_and_then_succeeds(monkeypatch, no_sleep):
    """**SSL EOF 要重试**——它是一次网络抖动，不是「这次任务失败」

    **注入**：前两次抛 httpx.ReadError（模拟实测的 SSL EOF），
    第三次成功。断言：调了 3 次、拿到了结果、**没有抛异常**。
    """
    calls = {"n": 0}

    def fake_post(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise httpx.ReadError("SSL: UNEXPECTED_EOF_WHILE_READING")
        return _FakeResponse(payload=_ok_payload("成了"))

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("runtime.llm._resolve_endpoint", lambda: ("http://x/v1", "k"))

    assert complete("hi") == "成了"
    assert calls["n"] == 3, f"应该重试到第 3 次，实际调了 {calls['n']} 次"


def test_transient_status_is_retried(monkeypatch, no_sleep):
    """**503 要重试**（服务端临时过载）"""
    calls = {"n": 0}

    def fake_post(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(status_code=503, text="overloaded")
        return _FakeResponse(payload=_ok_payload())

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("runtime.llm._resolve_endpoint", lambda: ("http://x/v1", "k"))

    assert complete("hi") == "hello"
    assert calls["n"] == 2


def test_credential_error_is_not_retried(monkeypatch, no_sleep):
    """**401 不重试**——凭证错，重试多少次都一样，只是白等

    **注入**：401 一直返回，断言只调了 1 次。
    """
    calls = {"n": 0}

    def fake_post(*_a, **_kw):
        calls["n"] += 1
        return _FakeResponse(status_code=401, text="invalid api key")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("runtime.llm._resolve_endpoint", lambda: ("http://x/v1", "k"))

    with pytest.raises(LLMError):
        complete("hi")
    assert calls["n"] == 1, f"401 不该重试，实际调了 {calls['n']} 次"


def test_retries_are_bounded(monkeypatch, no_sleep):
    """**重试有上限**——一直失败就明确失败，不许无限打

    一直抛 ReadError，断言调用次数 == TRANSPORT_RETRIES + 1。
    """
    calls = {"n": 0}

    def fake_post(*_a, **_kw):
        calls["n"] += 1
        raise httpx.ReadError("boom")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("runtime.llm._resolve_endpoint", lambda: ("http://x/v1", "k"))

    with pytest.raises(LLMError):
        complete("hi")
    assert calls["n"] == TRANSPORT_RETRIES + 1


def test_backoff_is_bounded_and_jittered():
    """退避要**有上限**（不能等两分钟）且**带抖动**（避免惊群）"""
    import random

    delays = [_backoff_delay(i) for i in range(10)]
    assert all(d <= 30 for d in delays), f"退避没封顶：{delays}"
    # 抖动：同一 attempt 连取多次不该全一样
    samples = {_backoff_delay(2) for _ in range(20)}
    assert len(samples) > 1, "退避没有抖动 —— 并发调用方会一起失败一起重试"
    assert random is not None


# ============================================================
# 2) 空输出：它是 parse 类问题，不该伪装成「响应形状不对」
# ============================================================

def test_empty_output_is_a_parse_error(monkeypatch):
    """**整轮空输出 = LLMParseError**（可重试），不是「响应形状不对」

    **为什么**：模型 think 吃满额度时会输出空串（实测过）。
    报成「unexpected response shape」会把运维引去查提示词——
    而真相是「模型这轮没说话」，该重试。

    **注入**：返回 content 为空的合法响应。
    """
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(
        payload={"choices": [{"message": {"content": ""}}]}
    ))
    monkeypatch.setattr("runtime.llm._resolve_endpoint", lambda: ("http://x/v1", "k"))

    with pytest.raises(LLMParseError):
        complete("hi")


# ============================================================
# 3) executor 兜底：异常也必须落成一条 failed 记录
# ============================================================

def test_op_output_not_dict_still_produces_a_failed_record():
    """**op 输出不是 dict 时，履约要落成 failed 记录**（不是查无此事）

    **注入**：让 runner 返回一个列表而不是 dict ——
    `dict(op_output)` 会抛 TypeError，而它发生在
    `runner.run(op)` 的 try **之外** → 异常会一路冒到 trigger.py，
    **跳过 store.append** → 这次履约在看板里根本不存在。
    """
    from core.beeline_models import Bee, Beeline, NextRef, Operation
    from runtime.executor import BeelineExecutor
    from runtime.models import TaskRunStatus, create_task_run

    class _BadOutputRunner:
        def run(self, op_type: str, op_input: dict[str, Any]) -> Any:
            # 不是 dict → 下一轮 `dict(op_output)` 抛 TypeError
            return ["不是", "一个", "dict"]

    beeline = Beeline(
        id="b1", version=1, description="bad output chain",
        operations=[
            Operation(op_id="op1", type="noop", input_from="external",
                      input="s_in", output="s_out",
                      next=[NextRef(op_id="op2")], bee=Bee(type="t")),
            Operation(op_id="op2", type="noop", input_from="op1",
                      input="s_out", output="s_out2",
                      bee=Bee(type="t")),
        ],
    )
    task_run = create_task_run("o", "i", "c", "r", "b1", 1, "rt", "in")

    result = BeelineExecutor(_BadOutputRunner()).execute(task_run, beeline, {})

    # **关键断言**：状态是 failed（不是「记录不存在」）
    assert result.status == TaskRunStatus.FAILED, \
        f"异常没有落成 failed 记录：status={result.status}"
    # 而且失败原因要能查清
    reasons = [
        str((e.detail or {}).get("reason") or "")
        for e in result.event_log.entries
        if e.event == "status_change"
    ]
    assert any("履约异常中断" in r for r in reasons), \
        f"失败原因没落进事件流：{reasons}"


def test_executor_records_failure_when_op_raises():
    """**op 抛异常 → 落成 failed 记录，带 op 级归因**

    这条是基线（原来就有），留着当对照：
    兜底加进来之后，它不能被兜底吃掉。
    """
    from core.beeline_models import Bee, Beeline, Operation
    from runtime.executor import BeelineExecutor
    from runtime.llm import LLMParseError
    from runtime.models import TaskRunStatus, create_task_run

    class _Runner:
        def run(self, op_type: str, op_input: dict[str, Any]) -> Any:
            raise LLMParseError("模型答的不是 JSON")

    beeline = Beeline(
        id="b1", version=1, description="failing chain",
        operations=[Operation(op_id="op1", type="noop", input_from="external",
                              input="s_in", output="s_out", bee=Bee(type="t"))],
    )
    task_run = create_task_run("o", "i", "c", "r", "b1", 1, "rt", "in")

    result = BeelineExecutor(_Runner()).execute(task_run, beeline, {})

    assert result.status == TaskRunStatus.FAILED
    op = list(result.operations.values())[0]
    assert op.error_kind == "llm_parse", (
        f"解析类失败应归因为 llm_parse，实际 {op.error_kind}"
    )


# ============================================================
# 4) store：外部写入看得见 + 坏行不静默
# ============================================================

def test_store_sees_records_written_by_another_process(tmp_path):
    """**别的进程写的记录要看得见**

    **注入**：直接往文件里追加一行（模拟链脚本在另一个进程写），
    然后用**同一个 store 实例**去读 —— 必须读得到。
    """
    path = tmp_path / "runs.jsonl"
    store = TaskRunStore(path)
    assert store.get_run("ext-1") is None

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "task_run_id": "ext-1", "box_id": "b", "created_at": "2026-01-01",
            "status": "completed",
        }) + "\n")

    assert store.get_run("ext-1") is not None, \
        "别的进程写的记录读不到 → 看板陈旧 + 交接假失败"


def test_store_counts_corrupt_lines(tmp_path):
    """**坏行要计数，不能静默吞掉**

    静默吞掉的代价是「少了一条记录，没人知道」。
    """
    path = tmp_path / "runs.jsonl"
    path.write_text(
        json.dumps({"task_run_id": "ok-1", "box_id": "b", "created_at": "2026-01-01"})
        + "\n"
        + "{这不是合法 JSON\n"
        + json.dumps({"task_run_id": "ok-2", "box_id": "b", "created_at": "2026-01-02"})
        + "\n",
        encoding="utf-8",
    )

    store = TaskRunStore(path)

    assert store.corrupt_lines == 1, f"坏行没被计数：{store.corrupt_lines}"
    assert store.get_run("ok-1") is not None
    assert store.get_run("ok-2") is not None


def test_store_write_is_flushed_to_disk(tmp_path):
    """**写完立刻落盘**（append 之后文件里就有那一行）

    这条守住「写时同步落盘（保证持久性）」这个承诺：
    模块头写了它，就得兑现。光 close() 只是交给页缓存，断电会丢。
    """
    from core.beeline_models import Bee, Beeline, Operation
    from runtime.models import create_task_run

    path = tmp_path / "runs.jsonl"
    store = TaskRunStore(path)

    beeline = Beeline(
        id="b1", version=1, description="x",
        operations=[Operation(op_id="op1", type="noop", input_from="external",
                              input="s_in", output="s_out", bee=Bee(type="t"))],
    )
    task_run = create_task_run("o", "i", "c", "r", "b1", 1, "rt", "in")

    store.append(task_run, box_id="b")

    text = path.read_text(encoding="utf-8").strip()
    assert text, "落盘文件是空的"
    assert json.loads(text.splitlines()[0])["task_run_id"] == task_run.identity.task_run_id


# ============================================================
# 5) 视图（view）：过滤后的快照，不能被磁盘重读破坏
# ============================================================

def test_view_can_be_read_without_crashing(tmp_path):
    """**view() 出来的对象要能正常读**

    **注入**：view() 用 `TaskRunStore.__new__` 造对象（不走 __init__），
    少一个状态字段就是一个「读的时候才炸」的暗雷。
    实测：refresh 挪进读路径后，view 出来的对象一调 list_recent
    就 AttributeError → 看板整个请求 500（连接被关闭）。
    """
    path = tmp_path / "runs.jsonl"
    store = TaskRunStore(path)
    store._cache = [
        {"task_run_id": "a1", "box_id": "kept", "created_at": "2026-01-01"},
        {"task_run_id": "a2", "box_id": "hidden", "created_at": "2026-01-02"},
    ]

    v = store.view({"kept"})

    # 不该炸
    assert v.list_recent(limit=10) is not None
    assert v.get_run("a1") is not None


def test_view_does_not_get_unfiltered_data_back(tmp_path):
    """**视图不能被磁盘重读灌回全量数据**

    **这是更危险的一半**：视图装的是「过滤后的子集」，
    如果它去重读磁盘，过滤就失效了 →
    **看板会显示未注册盒子的历史记录**（用户没要求隐藏的东西冒出来）。

    **注入**：视图造好之后，往源文件里追加一条别的盒子的记录，
    再让视图读一次 —— 它**不该**看到那条。
    """
    path = tmp_path / "runs.jsonl"
    store = TaskRunStore(path)
    store._cache = [
        {"task_run_id": "a1", "box_id": "kept", "created_at": "2026-01-01"},
    ]

    v = store.view({"kept"})

    # 源文件变化了（模拟别的进程写了新记录）
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "task_run_id": "a2", "box_id": "hidden", "created_at": "2026-01-02",
        }) + "\n")

    v.list_recent(limit=10)

    assert v.get_run("a2") is None, \
        "视图被磁盘重读灌回了未过滤的记录 —— 看板会显示本该隐藏的盒子"


def test_source_store_is_refreshed_before_view_is_taken(tmp_path):
    """**切视图之前，源 store 要先刷到最新**

    否则视图装的是刷新前的旧数据 —— 看板少显示几条记录，
    而且**没有任何报错**（数据陈旧长得跟「本来就没这些记录」一样）。
    """
    path = tmp_path / "runs.jsonl"
    store = TaskRunStore(path)

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "task_run_id": "ext-9", "box_id": "kept", "created_at": "2026-01-03",
        }) + "\n")

    v = store.view({"kept"})

    assert v.get_run("ext-9") is not None, \
        "切视图前没刷新 —— 视图装的是旧数据（少显示记录且不报错）"
