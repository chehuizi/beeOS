"""
boxes.receipt.runner - 小票识别盒的 3 个 operation 实现

**核心原则：LLM 只做「看图认字」，机械校验一律自己做。**

模型认出金额和条数之后，算术它一定也会编——qty×unit_price、加税、
求和这些它「顺手」就填好了，而它填错的时候不报错。所以这个盒子反过来：
模型只负责把图上印的字抄出来，所有等式由本模块按字符串→数值自己算。
这样「金额对不上」是能机械发现的，不是靠模型自觉。

三个 op：
1. read_receipt     —— 看图，抄出结构化字段（LLM）
2. verify_receipt   —— 算术与格式自洽（纯机械，零 LLM）
3. package_receipt  —— 合并成产出包（纯机械）
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from runtime.llm import complete_json
from runtime.runner import HandlerRunner


# LLM 只做这一件事：看图抄字。判据全部留给机械层。
_READ_PROMPT = """你是一张小票识别器。给你一张收银小票的照片，把它上面的信息抄成 JSON。

只抄图上**真实印着**的内容。看不清就填 null，不要猜、不要补全、不要编。

字段：
- merchant_name: 门店/商户名称
- receipt_no: 小票单号
- receipt_date: 日期，转成 YYYY-MM-DD
- receipt_time: 时间 HH:MM，看不到填 ""
- items: 商品行数组，每行 {{name, qty, unit_price, amount}}，价格是数字
- subtotal: 小计（商品合计，不含税）
- tax: 税额
- total: 实付合计
- payment_method: 支付方式（现金/微信/支付宝/银行卡…）
- low_confidence_fields: 你拿不准的字段名数组

**极其重要**：不要自己做算术校验，也不要为了"看起来对"而改数字。
qty、unit_price、amount、subtotal、tax、total 各自填图上印的值，
哪怕它们加不起来。加不起来是校验层该发现的事，不是你该偷偷修正的事。

只输出 JSON，不要解释。"""

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HH_MM = re.compile(r"^\d{1,2}:\d{2}$")


def _num(value: Any) -> float | None:
    """把模型给的数字洗干净：¥/$/逗号/全角 → float，拿不准就 None

    拿不准一律 None，不猜。宁可让算术校验失败，也不要把 12 读成 1.2。
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    s = re.sub(r"[^\d.\-]", "", value)
    if s in ("", "-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _simulate_read_receipt(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：看图抄字（真调 LLM，看不清就履约中止，不兜底）

    没有规则版降级：规则版读不了小票，退化成「解析出一堆 null 但流程全绿」
    恰恰是最坏结果——看起来成功，其实什么也没读到。
    """
    photo = input_data.get("photo")
    if not isinstance(photo, str) or not photo.strip():
        raise ValueError("receipt_photo.image is required")

    raw = complete_json(
        _READ_PROMPT,
        images=[photo],
        thinking="disabled",
        max_tokens=8000,
    )
    if not isinstance(raw, dict):
        raise ValueError("llm did not return a receipt object")
    # 套一层 parsed：下游 op 的输入 = 上游 op 的输出，
    # 不包装的话 verify 收到的是 task input（只有 photo），parsed 取不到。
    return {"parsed": raw}


def _simulate_verify_receipt(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：算术与格式自洽（纯机械，零 LLM）"""
    parsed = input_data.get("parsed") or {}
    items = parsed.get("items") or []
    if not isinstance(items, list):
        items = []

    # 字段完整性：金额三元组 + 至少能定位到商户或单号
    subtotal = _num(parsed.get("subtotal"))
    tax = _num(parsed.get("tax"))
    total = _num(parsed.get("total"))
    has_identity = bool(parsed.get("merchant_name")) or bool(parsed.get("receipt_no"))
    field_completeness = (
        subtotal is not None and total is not None and has_identity and len(items) > 0
    )

    # 逐行 qty × unit_price == amount（容差 0.01：浮点 + 两位小数四舍五入）
    line_bad = []
    line_sum = 0.0
    for i, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            line_bad.append(i)
            continue
        qty, price, amt = _num(it.get("qty")), _num(it.get("unit_price")), _num(it.get("amount"))
        amt_ok = (
            qty is not None and price is not None and amt is not None
            and abs(qty * price - amt) <= 0.01
        )
        if not amt_ok:
            line_bad.append(i)
        if amt is not None:
            line_sum += amt
    line_arithmetic = not line_bad

    # Σ行金额 == 小计
    sum_arithmetic = (
        subtotal is not None and abs(line_sum - subtotal) <= 0.01
    )

    # 小计 + 税 == 合计（没税就当 0）
    total_arithmetic = (
        subtotal is not None and total is not None
        and abs(subtotal + (tax or 0.0) - total) <= 0.01
    )

    # 日期格式
    date_raw = parsed.get("receipt_date") or ""
    date_ok = bool(_ISO_DATE.match(str(date_raw)))
    if date_ok:
        try:
            datetime.strptime(str(date_raw), "%Y-%m-%d")
        except ValueError:
            date_ok = False
    time_raw = parsed.get("receipt_time") or ""
    time_ok = time_raw in ("", None) or bool(_HH_MM.match(str(time_raw)))
    date_wellformed = date_ok and time_ok

    return {
        "parsed": parsed,
        "field_completeness": field_completeness,
        "line_arithmetic": line_arithmetic,
        "sum_arithmetic": sum_arithmetic,
        "total_arithmetic": total_arithmetic,
        "date_wellformed": date_wellformed and time_ok,
        # 总口径：全部机械判据都过
        "totals_match": (
            field_completeness and line_arithmetic and sum_arithmetic
            and total_arithmetic and date_wellformed and time_ok
        ),
        "line_errors": line_bad,
    }


def _simulate_package_receipt(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：合并成产出包（纯机械）"""
    v = input_data
    parsed = v.get("parsed") or {}
    items = parsed.get("items") or []
    normalized_items = []
    for i, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            continue
        normalized_items.append({
            "line_no": i,
            "name": it.get("name") or "",
            "qty": _num(it.get("qty")) or 0.0,
            "unit_price": _num(it.get("unit_price")) or 0.0,
            "amount": _num(it.get("amount")) or 0.0,
        })

    def n(key: str) -> float:
        v2 = _num(parsed.get(key))
        return v2 if v2 is not None else 0.0

    return {
        "merchant_name": parsed.get("merchant_name") or "",
        "receipt_no": parsed.get("receipt_no") or "",
        "receipt_date": parsed.get("receipt_date") or "",
        "receipt_time": parsed.get("receipt_time") or "",
        "items": normalized_items,
        "subtotal": n("subtotal"),
        "tax": n("tax"),
        "total": n("total"),
        "payment_method": parsed.get("payment_method") or "",
        "field_completeness": bool(v.get("field_completeness")),
        "line_arithmetic": bool(v.get("line_arithmetic")),
        "sum_arithmetic": bool(v.get("sum_arithmetic")),
        "total_arithmetic": bool(v.get("total_arithmetic")),
        "date_wellformed": bool(v.get("date_wellformed")),
        "totals_match": bool(v.get("totals_match")),
        "low_confidence_fields": parsed.get("low_confidence_fields") or [],
    }


RECEIPT_HANDLERS: dict[str, Any] = {
    "read_receipt": _simulate_read_receipt,
    "verify_receipt": _simulate_verify_receipt,
    "package_receipt": _simulate_package_receipt,
}

RUNNER = HandlerRunner(RECEIPT_HANDLERS, owner="receipt_capture_box")