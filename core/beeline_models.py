"""
core.beeline_models - beeline 顶层数据结构

按 [beebox-beeline-schema.md] 定义：
  beeline = 1 类任务的标准作业路线，本质是 1 张有向图
  operation 是节点，next 字段是节点之间的有向边（顺序 / 并发 / 分支）

beeline 独立维护（不归 beeBox definition 管）；
definition 通过 task[].beeline_id + beeline_version 引用。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ============================================================
# operation 内部资源
# ============================================================


class Bee(BaseModel):
    """operation 调用的 bee"""

    type: str = Field(description="bee 类型标识")
    params: dict[str, Any] = Field(default_factory=dict)


class ExternalSystem(BaseModel):
    """operation 调用的外部系统"""

    id: str = Field(description="外部系统标识")
    interface: str = Field(description="调用的接口 / 方法")


class Idempotency(BaseModel):
    """operation 幂等性配置

    required  —— 重复执行必须产生同结果（runtime 用 key 去重）
    optional  —— 不强制幂等，runtime 允许重试（默认）
    forbidden —— 明确禁止重试（如通知类 op 重复执行会重复发邮件）
    """

    mode: Literal["required", "optional", "forbidden"]
    key: Optional[str] = Field(default=None, description="幂等键路径（mode=required 时必填）")


# ============================================================
# operation 编排
# ============================================================


class NextRef(BaseModel):
    """operation 的下一跳引用"""

    op_id: str = Field(description="目标 operation 的 op_id")
    when: Optional[str] = Field(default=None, description="条件表达式（无 = 无条件顺序）")


class Operation(BaseModel):
    """beeline 内的单个 operation（节点）"""

    op_id: str = Field(description="operation 在该 beeline 内唯一")
    type: str = Field(description="operation 类型（业务化标识）")
    input_from: str = Field(description="数据来源：external = task 输入；或上一个 op_id")
    input: str = Field(description="input 数据结构引用（schema id）")
    output: str = Field(description="output 数据结构引用（schema id）")
    next: list[NextRef] = Field(default_factory=list)

    idempotency: Optional[Idempotency] = None
    bee: Optional[Bee] = None
    external_system: Optional[ExternalSystem] = None

    @model_validator(mode="after")
    def validate_resource(self) -> "Operation":
        if self.bee is None and self.external_system is None:
            raise ValueError(
                f"operation '{self.op_id}' must have at least one of bee / external_system"
            )
        if self.idempotency and self.idempotency.mode == "required" and not self.idempotency.key:
            raise ValueError(
                f"operation '{self.op_id}' has idempotency.mode=required but no key"
            )
        return self


# ============================================================
# beeline 顶层
# ============================================================


class Beeline(BaseModel):
    """beeline 顶层结构

    业务语义：
    - beeline = 1 类任务的标准作业路线（procedure 实现）
    - 通过 id + version 独立维护
    - 1 张有向图（operation 节点 + next 边）
    """

    id: str = Field(description="beeline 唯一标识")
    version: int = Field(description="beeline 版本（递增序列号）")
    description: str

    operations: list[Operation] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> "Beeline":
        # op_id 唯一
        ids = [op.op_id for op in self.operations]
        if len(ids) != len(set(ids)):
            dupes = [i for i in ids if ids.count(i) > 1]
            raise ValueError(f"beeline '{self.id}' has duplicate op_id: {set(dupes)}")

        op_id_set = set(ids)

        # next.op_id 必须指向真实存在的 op
        for op in self.operations:
            for nxt in op.next:
                if nxt.op_id not in op_id_set:
                    raise ValueError(
                        f"operation '{op.op_id}' next references unknown op_id '{nxt.op_id}'"
                    )

            # input_from 合法
            if op.input_from != "external" and op.input_from not in op_id_set:
                raise ValueError(
                    f"operation '{op.op_id}' input_from='{op.input_from}' "
                    f"must be 'external' or a real op_id"
                )

        # 无环（DAG 校验：拓扑排序）
        self._check_acyclic()

        return self

    def _check_acyclic(self) -> None:
        """检测有向图是否有环（DFS 三色标记）"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {op.op_id: WHITE for op in self.operations}

        def dfs(node: str, stack: list[str]) -> None:
            color[node] = GRAY
            stack.append(node)
            op = self.get_operation(node)
            assert op is not None
            for nxt in op.next:
                if color[nxt.op_id] == GRAY:
                    cycle = " -> ".join(stack + [nxt.op_id])
                    raise ValueError(
                        f"beeline '{self.id}' has cycle: {cycle}"
                    )
                if color[nxt.op_id] == WHITE:
                    dfs(nxt.op_id, stack)
            color[node] = BLACK
            stack.pop()

        for op in self.operations:
            if color[op.op_id] == WHITE:
                dfs(op.op_id, [])

    def get_operation(self, op_id: str) -> Optional[Operation]:
        """按 op_id 查找 operation"""
        for op in self.operations:
            if op.op_id == op_id:
                return op
        return None

    def get_start_operations(self) -> list[Operation]:
        """返回所有入口 operation（没有任何 op 的 next 指向自己）"""
        targeted = {nxt.op_id for op in self.operations for nxt in op.next}
        return [op for op in self.operations if op.op_id not in targeted]