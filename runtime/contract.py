"""
runtime.contract - 投料契约校验（Structural Contract Gate）

按 [beebox-design.md §3.4] 实现履约前第一道闸：

    投料口 → 结构契约校验 → BeelineExecutor → acceptance → 落盘

设计边界（重要）：
- 这一层只回答"形状对不对"——字段缺没缺、类型对不对、枚举值合不合法
- 这一层**不回答**"内容够不够"——那是 acceptance 的业务验收职责
- 因此本模块不 import 任何 boxes/ 业务知识：runtime 不该知道
  "业务流程 / 业务规则"怎么定义，那是 definition 声明的业务语义

一个空需求集在结构上是合法的（0 条违规）——它能通过本闸，然后在
acceptance 被业务判据拦下。两个闸各管一段，不互相替代。
"""

from __future__ import annotations

from typing import Any

from core.models import FieldDef, SchemaDef


# ============================================================
# 单字段校验
# ============================================================

# bool 是 int 的子类，python 里 isinstance(True, int) 为 True，
# 数字校验时必须先把 bool 排除，否则 boolean 会被当成 integer 放过
_PY_TYPE_CHECKS: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, (list, tuple)),
}


def _check_scalar(field: FieldDef, value: Any, path: str, errors: list[str]) -> None:
    """校验标量字段（string / number / integer / boolean / enum）"""
    if field.type == "enum":
        if not isinstance(value, str):
            errors.append(f"{path}: expected enum string, got {type(value).__name__}")
        elif field.values and value not in field.values:
            errors.append(
                f"{path}: '{value}' not in {sorted(str(v) for v in field.values)}"
            )
        return

    check = _PY_TYPE_CHECKS.get(field.type)
    if check is None:  # ref:<schema_id> 等暂不做深校验，放行
        return
    if not check(value):
        errors.append(
            f"{path}: expected {field.type}, got {type(value).__name__}"
        )


def _check_field(
    field: FieldDef, value: Any, path: str, errors: list[str]
) -> None:
    """校验单个字段（含 object / array 递归）"""
    if value is None:
        # required=True 时显式 None 视同缺失；缺字段由上层 required 检查兜
        return

    if field.type == "object":
        if not isinstance(value, dict):
            errors.append(
                f"{path}: expected object, got {type(value).__name__}"
            )
            return
        for sub in field.properties or []:
            if sub.name not in value:
                if sub.required:
                    errors.append(f"{path}.{sub.name}: required field missing")
                continue
            _check_field(sub, value[sub.name], f"{path}.{sub.name}", errors)
        # 未声明的多余字段不报错（模型可扩展）
        return

    if field.type == "array":
        if not isinstance(value, (list, tuple)):
            errors.append(
                f"{path}: expected array, got {type(value).__name__}"
            )
            return
        if field.items is not None:
            for i, item in enumerate(value):
                _check_field(field.items, item, f"{path}[{i}]", errors)
        return

    _check_scalar(field, value, path, errors)


# ============================================================
# 对外：按 SchemaDef 校验 payload
# ============================================================


def validate_payload(payload: Any, schema: SchemaDef) -> list[str]:
    """按业务 schema 校验投料 payload

    Args:
        payload: 投料内容（dict）
        schema: definition.schemas 里的 schema 定义

    Returns:
        错误描述列表；空列表 = 通过结构契约
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return [f"payload: expected object, got {type(payload).__name__}"]

    for field in schema.fields:
        if field.name not in payload:
            if field.required:
                errors.append(f"{field.name}: required field missing")
            continue
        _check_field(field, payload[field.name], field.name, errors)

    return errors
