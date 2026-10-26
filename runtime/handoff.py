"""
runtime.handoff - 盒间接力：把上游盒子的产出接到下游盒子的投料里

**为什么要有这个**：6 只盒子之间原本**没有任何数据通道**——
`placement.feeds_into` 只是看板上的箭头，投料要人手工把上一只盒子的
产出 copy 进下一只盒的 payload。于是「一条需求走完全链」只能靠人操作，
而且每次手工 copy 的形状可能不一样（少拷一个字段、下游照样收）。

**它做什么**：`handoff` 声明「目标投料的哪个字段 ← 上游哪次履约的产出」，
本模块按目标盒子**自己声明的** schema 把上游产出校验一遍再接上。

**形状由消费者声明**：目标盒的投料 schema 里 `ref:<schema_id>` 指向
哪个 schema，就用哪个 schema 校验上游产出。所以 dev 盒校验的是
「设计包是不是满足 **dev 声明的** 那份形状」，不是「设计盒产出什么就收什么」。

**本模块不含业务知识**：它不知道设计包长什么样、也不知道 WMS 是什么。
形状从参数进来，产出从 store 进来。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from core.models import SchemaDef
from runtime.contract import validate_payload


class HandoffError(ValueError):
    """接力失败：上游产出接不到下游投料上"""


def _resolve_ref(schema_id: str, schemas: Any) -> Optional[SchemaDef]:
    if not schemas:
        return None
    if isinstance(schemas, dict):
        found = schemas.get(schema_id)
        return found if isinstance(found, SchemaDef) else None
    for s in schemas:
        if isinstance(s, SchemaDef) and s.id == schema_id:
            return s
    return None


def assemble_handoff(
    payload: dict[str, Any],
    handoff: Optional[dict[str, str]],
    *,
    task_schema: SchemaDef,
    schemas: Any,
    get_run: Callable[[str], Optional[dict[str, Any]]],
) -> dict[str, Any]:
    """按 handoff 声明组装投料

    Args:
        payload: 手工投的料（会被复制一份，不改原对象）
        handoff: {目标投料字段名: 上游 task_run_id}
        task_schema: 目标盒子的投料 schema
        schemas: 目标盒子的全部 schemas（用来解析 ref:）
        get_run: 取上游履约记录的函数（通常是 store.get_run）

    Returns:
        新的投料 dict

    Raises:
        HandoffError: 字段不存在 / 不是 ref 类型 / 上游不存在 / 上游产出形状不对
    """
    if not handoff:
        return dict(payload)

    fields = {f.name: f for f in task_schema.fields}
    out = dict(payload)

    for field_name, run_id in handoff.items():
        if field_name not in fields:
            raise HandoffError(
                f"handoff 指向的字段 {field_name!r} 不在 task_schema "
                f"{task_schema.id!r} 里（现有字段：{sorted(fields)}）"
            )
        field = fields[field_name]
        if not field.type.startswith("ref:"):
            raise HandoffError(
                f"handoff 只能填 ref: 类型的字段，{field_name!r} 是 "
                f"{field.type!r}。普通字段自己传——"
                f"让交接通道去猜它该接上游的哪一块，就是在猜业务约定"
            )
        if out.get(field_name) is not None:
            raise HandoffError(
                f"投料里已经有 {field_name!r} 的值，handoff 又要接一次。"
                f"一个字段两个来源 = 说不清哪个是真的。要交接就别手填"
            )

        record = get_run(str(run_id))
        if record is None:
            raise HandoffError(
                f"handoff {field_name!r} 指向的上游履约 {run_id!r} 找不到"
            )
        if record.get("status") == "failed":
            # **带上失败原因**：只说「失败了」等于让下游的人去猜。
            # 上游已经知道为什么不接（op 的错误文本），不传下来就是
            # 「算了却看不见」——链路断在哪儿只能靠猜。
            reason = str(record.get("error_reason") or "").strip()
            detail = f"：{reason}" if reason else ""
            raise HandoffError(
                f"handoff {field_name!r} 指向的上游履约 {run_id!r} 失败了{detail}，"
                f"接过来的产出不可信"
            )
        artifact = record.get("artifact")
        if not artifact:
            raise HandoffError(
                f"handoff {field_name!r} 指向的上游履约 {run_id!r} 没有产出"
            )

        target_id = field.type[len("ref:"):]
        target = _resolve_ref(target_id, schemas)
        if target is None:
            raise HandoffError(
                f"{task_schema.id!r} 里的 {field_name!r} 声明 "
                f"{field.type!r}，但该 schema id 不在本盒的 schemas 里——"
                f"ref 只能引用本 definition 内（见 docs/beebox-definition-schema.md）"
            )

        errors = validate_payload(artifact, target, schemas=schemas)
        if errors:
            raise HandoffError(
                f"上游履约 {run_id!r} 的产出不符合 {task_schema.id!r} 里 "
                f"{field_name!r} 声明的形状 {target_id!r}："
                + "; ".join(errors[:5])
            )

        out[field_name] = artifact

    return out
