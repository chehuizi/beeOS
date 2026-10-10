"""
runtime.runner - operation 分派机制

**这个模块不含任何业务知识。**

它只做一件事：按 `op.type` 找到该跑的 handler 并执行。
handler 从哪来、干什么，是盒子的事，不由内核持有。

以前这里叫 `mock_runner`，里面塞了三只盒子的全部业务实现
（订单异常盒 8 个 op、建模盒 7 个 op + DDD 模式映射，
还有从 `capture_runner` import 进来并表的捕获盒 5 个 op）。
后果是内核比任何一只盒子都懂业务——加一只新盒子就得改内核，
而盒子带不走自己的实现。

现在机制和业务分开了：
- `runtime/runner.py`：分派协议（这里）
- `boxes/*/runner.py`：某只盒子的 op 实现 + 它自己的 RUNNER 实例
- `kanban/trigger.py`：按 box_id 取对应的 RUNNER 交给 executor

盒子是可插拔单元的判据：删掉 `boxes/` 下任意一只盒子目录，
`runtime/` 仍然跑得起来。
"""

from __future__ import annotations

from typing import Any, Callable, Optional


Handler = Callable[[dict[str, Any]], dict[str, Any]]


class HandlerRunner:
    """按 op.type 分派的 operation 执行器

    纯机制：只认自己拿到的那张表，表里有什么它不知道、也不该知道。

    Args:
        handlers: op.type → handler 的映射（由盒子提供）
        owner: 这张表属于谁。错误信息里报出来，便于定位到是哪只盒子缺实现。
    """

    def __init__(self, handlers: dict[str, Handler], *, owner: str = "") -> None:
        self._handlers: dict[str, Handler] = dict(handlers)
        self._owner = owner

    def run(
        self,
        op_type: str,
        input_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """执行一个 operation

        Raises:
            NotImplementedError: op.type 没在这张表里。
                不静默返回空——空输出会被下游当成「算出来是空的」。
        """
        handler = self._handlers.get(op_type)
        if handler is None:
            who = f" in {self._owner}" if self._owner else ""
            raise NotImplementedError(
                f"no handler for op_type={op_type!r}{who}. "
                f"known op types: {sorted(self._handlers)}"
            )
        return handler(input_data or {})

    def supports(self, op_type: str) -> bool:
        return op_type in self._handlers

    @property
    def owner(self) -> str:
        return self._owner