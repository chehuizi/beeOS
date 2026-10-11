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

import base64
import binascii
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
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
- subtotal: 小计 / 商品合计
- tax: 税额。**零售 POS 小票通常没有这一栏**（超市、便利店 POS 单是含税价），
  找不到就填 null，**不要拿别的数字凑**。
- discount: 优惠 / 折扣 / 减免 / 抹零，填**正数**（票上印 -0.18 就填 0.18）。
  常见标签：支付宝优惠、微信优惠、满减、折扣、抹零、立减。
- total: 实付合计（支付明细那一行）
- payment_method: 支付方式（现金/微信/支付宝/银行卡/碰一下支付…）
- low_confidence_fields: 你拿不准的字段名数组

**抄，不是算。** qty、unit_price、amount、subtotal、tax、discount、total 各自填
**图上印着的那一格**，不管它们之间算不算得起来。

**不要自己做算术校验**，也不要为了「看起来对」而改数字。加不起来是校验层该发现的事，
不是你该偷偷修正的事——你一修正，这条校验就再也发现不了真实错误了。

**qty 只看「数量」那一栏。** 票上常写成 `x 1`、`×1`、`1.00` 这类形式。
**规格里的数字不是数量**：实测一张「李锦记 薄盐生抽(酿造酱油) **500ml*2**」的单子，
`500ml*2` 是规格（500ml 的瓶子 ×2），数量栏印的是 `x 1`、金额 14.90。
模型从规格里捡了个「2」当数量，金额也跟着抄错。
同理「6 联」「*12」「2.5L」都是规格，不是数量。

**amount 抄「金额」那一栏，不要用 qty × unit_price 代替。** 两者本来就是该被校验的
两个独立数字，你拿一个去填另一个，这条校验就永远发现不了错。

看不清就填 null，不要猜、不要补全、不要编。

只输出 JSON，不要解释。"""

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HH_MM = re.compile(r"^\d{1,2}:\d{2}$")

# 模型接口 + 浏览器两边都不认的格式，进盒子先转成 JPEG
_NEEDS_CONVERSION = {"image/heic", "image/heif"}


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


def _as_viewable_image(photo: str) -> str:
    """HEIC 转成 JPEG 再交给模型；别的格式原样透传

    **为什么转在盒子里而不是内核**：HEIC 是 iPhone 拍的票据，是这个盒子的
    业务事实（小票就是这么来的），不是「系统层面都要处理图片格式」。
    放进 core 就等于让内核替所有盒子背一条它们用不上的规矩。

    **为什么非得转**：两个独立的原因，少一个都还能忍，两个叠一起就绕不过——
      1. 模型接口直接 400：invalid param: image data url media type
         ["image/heic"] not supported（确定性拒绝，重试无用）
      2. Chrome 解不了 HEIC，投料预览是一张破图。传出去的是 JPEG、
         PNG 还是 HEIC，取决于下游要什么，不取决于相机怎么存。
    """
    head, _, b64 = photo.partition(",")
    if "base64" not in head:
        return photo  # 不是 data URL（http 直链之类），本地无从转，原样交给下游
    mime = head[len("data:"):].split(";")[0].strip().lower()
    if mime not in _NEEDS_CONVERSION:
        return photo
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"HEIC 图片 base64 解不开：{exc}") from exc
    return "data:image/jpeg;base64," + base64.b64encode(
        _heic_to_jpeg(raw, mime)
    ).decode("ascii")


def _heic_to_jpeg(raw: bytes, mime: str) -> bytes:
    """HEIC/HEIF 字节 → JPEG 字节

    用 macOS 自带的 `sips`：零依赖、零安装、HEVC 解码是系统级的。
    少一个可选的依赖（装不上 Pillow——这台机器的 .venv 里没有 pip），
    也少一份要维护的编解码器实现。

    缺 sips / 转不动都如实报错，不悄悄退回「原样发出去」——那等于把
    400 原封不动还给用户，只是晚了一拍。
    """
    sips = shutil.which("sips")
    if sips is None:
        raise ValueError(
            f"这台机器没有 sips（macOS 自带的图片转换工具），转不了 {mime}。"
            "请把票据另存为 JPEG 再投。"
        )
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in"
        dst = Path(tmp) / "out.jpg"
        src.write_bytes(raw)
        # 不用 shell=True、不经管道：参数是固定字面量，没有拼接用户输入
        proc = subprocess.run(
            [sips, "-s", "format", "jpeg", str(src), "--out", str(dst)],
            capture_output=True, timeout=60, check=False,
        )
        if not dst.is_file() or dst.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout or b"").decode(
                "utf-8", "replace"
            ).strip()
            raise ValueError(
                f"{mime} 转 JPEG 失败：{detail or f'sips 退出码 {proc.returncode}'}"
            )
        return dst.read_bytes()


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
        images=[_as_viewable_image(photo)],
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

    # 小计 + 税 − 优惠 = 实付
    # 优惠这一项是零售 POS 小票的标配（支付宝优惠 / 满减 / 抹零），而 POS 小票
    # **没有税**。以前公式只写「小计 + 税 = 实付」，票上那个优惠数就无处安放，
    # 模型只能给 null，于是每张打过折的票都必然转红。
    # 两种票据都在这套公式里：POS 小票 tax 缺省 0、discount 有值；
    # 增值税发票 tax 有值、discount 缺省 0。
    discount = _num(parsed.get("discount"))
    total_arithmetic = (
        subtotal is not None and total is not None
        and abs(subtotal + (tax or 0.0) - (discount or 0.0) - total) <= 0.01
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
            # 拿不到就留 None，不补 0.0。
            # 「模型没读到这一格」和「这一格真的是 0」是两件事：
            # 编成 0 等于替模型做了个它没依据的断言，而这份产出是给人
            # 人工对账用的——有人在票上找 0.00 的小计，能白找半天。
            "qty": _num(it.get("qty")),
            "unit_price": _num(it.get("unit_price")),
            "amount": _num(it.get("amount")),
        })

    return {
        "merchant_name": parsed.get("merchant_name") or "",
        "receipt_no": parsed.get("receipt_no") or "",
        "receipt_date": parsed.get("receipt_date") or "",
        "receipt_time": parsed.get("receipt_time") or "",
        "items": normalized_items,
        "subtotal": _num(parsed.get("subtotal")),
        "tax": _num(parsed.get("tax")),
        "discount": _num(parsed.get("discount")),
        "total": _num(parsed.get("total")),
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