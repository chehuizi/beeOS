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
import json
import math
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from core.models import NOT_APPLICABLE, is_not_applicable
from runtime.llm import complete_json
from runtime.runner import HandlerRunner


def _keep_state(value: Any) -> Any:
    """判据值三态透传：True / False / "n/a"

    不能用 bool() 兜——bool("n/a") 是 True，「用不上」会被悄悄变成「通过」。
    """
    return NOT_APPLICABLE if is_not_applicable(value) else bool(value)


# ---- 行级折扣 ----
#
# 为什么单独一块：折后商品的票面长这样（MUJI POS 实测）
#     1x498×5.83折=290.00   →  ¥290.0
# 原价 498、折后行金额 290、折扣 5.83 折。三个数**都印在票上**，
# 抄下来全对，但 `数量 × 原价 ≠ 金额`。以前这条判据只能转红，
# 而产出其实一点错没有——同一个病根的第三次：把某类票的形状
# 当成所有票的形状（前面两次是没税栏、没单价栏）。
#
# 折扣率是**读来的**，不是从金额倒推的。倒推的话
# `amount / (qty*price)` 恒等于它自己，这条判据就废了——
# 那才是真正的「永远绿的判据」。


def _discount_multiplier(raw: Any) -> float:
    """票面折数 → 支付比例。「5.83折」→ 0.583，「8折」→ 0.8

    返回 None 表示「这一行没折扣」，调用方按全价验。

    只认 0 < 折数 < 10。写 10（= 不打折）是模型在编——
    「没有折扣」的诚实写法是 null，编一个 10 出来会让真正的
    漏抄折扣看起来像正常的全价行。
    """
    zhe = _num(raw)
    if zhe is None or not (0 < zhe < 10):
        return None
    return zhe / 10.0


def _line_amount_ok(
    qty: float, price: float, multiplier: float | None, amount: float
) -> bool:
    """行金额对不对得上（含行级折扣 + 抹零）

    两种合法：

    1. 算得开——`数量 × 原价 × 折扣` 直接等于行金额（容差 0.01，
       留给浮点和两位小数）。

    2. 抹零——小票常把行金额**抹零到元**。实测 MUJI：
       498 × 5.83折 = 290.334，票面印的是 290.00。
       这不是算错，是 POS 在这一行就取整了。

    为什么把抹零写成判据的一部分而不是一句「容差放宽点」：
    写成规则，误差就有上限（<1 元），金额读错 0.9 元这种真错
    仍然会被拦；写成模糊容差，容差就成了没人敢动的橡皮。
    它的盲区也照实说：金额被误读成比正确值小不到 1 元时会放过。
    """
    expected = qty * price * (1.0 if multiplier is None else multiplier)
    if abs(expected - amount) <= 0.01:
        return True
    # 抹零到元（向下取整）
    return math.floor(expected + 1e-9) == amount


# LLM 只做这一件事：看图抄字。判据全部留给机械层。
_READ_PROMPT = """你是一张小票识别器。给你一张收银小票的照片，把它上面的信息抄成 JSON。

只抄图上**真实印着**的内容。看不清就填 null，不要猜、不要补全、不要编。

字段：
- merchant_name: 门店/商户名称
- receipt_no: 小票单号
- receipt_date: 日期，转成 YYYY-MM-DD
- receipt_time: 时间 HH:MM，看不到填 ""
- items: 商品行数组，每行 {{name, qty, unit_price, amount, discount_rate}}，价格是数字
  discount_rate 只在票面**这一行**印了折扣时才填，照抄折数：「×5.83折」填 5.83、
  「8折」填 8。没有折扣就填 null。**不要填 10**——10 是「没折扣」的编造说法。
  它是**行**上的折扣，不是小票底部的「优惠金额」栏（那个填进顶部的 discount）。
- subtotal: 小计 / 商品合计 / **合计**。标签是「合计」也要填——
  实测六贤记烘焙单印的就是「合计:39.70(2.60)」，只认「小计」两个字
  会把它当成实付、subtotal 留空，于是字段完整性和合计校验一起转红。
- tax: 税额。**零售 POS 小票通常没有这一栏**（超市、便利店 POS 单是含税价），
  找不到就填 null，**不要拿别的数字凑**。
- discount: 优惠 / 折扣 / 减免 / 抹零，填**正数**（票上印 -0.18 就填 0.18）。
  常见标签：支付宝优惠、微信优惠、满减、折扣、抹零、立减。
- included_discount: **合计里已经含进去的**优惠额。只在票面写成
  `合计:39.70(2.60)` 这种「合计(括号里有数)」的样子时才填括号里那个**金额**。
  实测六贤记：`合计:39.70(2.60)` / `折让金额:2.60` / `付款 39.70`——
  折让是**含在** 39.70 里的，不是从 39.70 里减掉的，填 2.60。
  盒马那种 `商品合计 28.80` 后面没有括号，优惠是另减的，填 null。
  **括号里是百分数的不要填**（MUJI 的 `实物总额(9%)` 是税率不是金额），
  **分店名、规格之类的括号也不要填**。拿不准就填 null。
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

**unit_price 抄原价，amount 抄折后行金额，两个都要照抄。** 折后商品的票面长这样：
`1x498×5.83折=290.00` —— 498 是原价、290.00 是这一行实收、5.83 是折扣。
这三个数都对，**1 × 498 ≠ 290 不是错**。你要是看着不平就把 unit_price 改成 290，
折扣就凭空消失了，这条校验也就再也发现不了「优惠金额」栏漏抄。别这么干。

看不清就填 null，不要猜、不要补全、不要编。

**每一行的单价，只从「这一行自己的」数字里抄。** 小票上最容易被抓错位的就是它——
「商品合计 / 订单总额 / 实付」那几个数长得最显眼，位置又紧挨着明细，
很容易顺手填进某一行的单价里。实测一张 NB 小票：第二行单价明明印着
「（4.50元/瓶）」，被抄成了 8.4——8.4 是整张票的商品合计。
**合计、订单总额、实付这三个数，一个都不能当成任何一行的单价。**
抄完扫一遍：有哪一行的单价跟这张票的合计撞上了，就把它改回本行括号里那个数。

**low_confidence_fields 要真的用起来。** 抄完回头看一眼：哪一行的
数量 × 单价跟这一行的金额对不上、哪个数你觉得眼熟但没底，就把它填进去。
留空等于没看——人工对账只核你标出来的那些。

只输出 JSON，不要解释。"""


# 第二遍只问「对不上的那几行」，不是重读整张票
_REREAD_PROMPT = """你刚才抄了一张小票，有几处抄得不对。请重新看一眼那张图，**只修正这几处**。

{problem}

怎么读这些栏：
- 数量抄「数量」那一栏（票上常写成 `x 1`、`×1`、`1.00`）
- 单价抄「（3.90元/瓶）」这类括号里的数字，或「单价」那一栏
- 金额抄「金额」那一栏
- 折扣抄「×5.83折」里的折数
- 日期抄票面**抬头时间那一行**里的日期部分，写成 YYYY-MM-DD。
  票上印成 `2026.10.10` / `2026/10/10` / `2026年10月10日` 都行，
  你只要把那三组数字抄对，换算不用你做。
  **单号是另一个字段**（`NO.` 开头那串数字）——
  上一遍最常见的错就是把整行 `2026.10.10 20:36` 塞进单号，
  结果单号是日期、日期反而空着。两者别搞混。

**最要紧的一条：重新抄，不是重新算。**
你可能会想「让这几行对上」——**不要**。数字不是用来凑的，是用来抄的。
把单价改成金额能让它平，但票上印的是什么就是什么，抄错了就该是错的。
对不上就在 low_confidence_fields 里写上对应字段名，那比改数字有用得多。

只输出 JSON，不要解释。格式：
{{"修正": [{{"行号": 1, "数量": null, "单价": null, "金额": null, "折扣": null}}],
  "补字段": {{"receipt_date": null}},
  "low_confidence_fields": ["字段名"]}}"""

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HH_MM = re.compile(r"^\d{1,2}:\d{2}$")
# 小票上的日期分隔符：点、斜杠、中文年月日都算
_DATE_SEPARATORS = re.compile(r"[./年月日]")

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


def _normalize_date(value: Any) -> str:
    """把小票上的日期机械归一成 YYYY-MM-DD；认不出写法返回空串

    票面印的分隔符五花八门：六贤记那张是 `2026.10.10`，还有 `2026/10/10`、
    `20261010`、`2026年10月10日`，有时后面粘着时间 `2026.10.10 20:36`。

    以前判据只认 `YYYY-MM-DD`，等于**要求模型每次都乖乖转换**——而模型对
    格式的服从度远低于对数字的服从度。实测：它把整行 `2026.10.10 20:36`
    塞进了 receipt_no、让 receipt_date 空着，判据红。
    同一个病根的**第六次**：把某类票的形状当成所有票的形状
    （前五次是没税栏、没单价栏、有行级折扣、没小计栏、折让已含在合计里）。

    所以归一放在机械层：换分隔符、砍掉粘着的时间、补两位年，
    再交给 strptime 判真假。模型只管把那三组数字抄对。

    **认出写法 ≠ 认可内容**——`2026.13.45` 归一完照样解析失败、照样转红。
    归一只放宽「写法」，不放宽「日期本身合不合法」。
    """
    s = str(value or "").strip()
    if not s:
        return ""
    # 砍掉粘在后面的时间：`2026.10.10 20:36` / `2026/10/10T20:36`
    s = re.split(r"[\sT]", s, maxsplit=1)[0]
    s = _DATE_SEPARATORS.sub("-", s)
    # 「日」及其后可能跟的尾巴（`10日(周二)`）；括号里的补充说明也砍掉
    s = re.sub(r"[日号].*$", "", s)
    s = re.sub(r"[（(].*?[）)]", "", s)
    s = s.strip("-")
    if not s:
        return ""
    # 紧凑写法 `20261010`
    if s.isdigit() and len(s) == 8:
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    parts = s.split("-")
    if len(parts) == 3 and len(parts[0]) == 2 and parts[0].isdigit():
        # 两位年 `26.10.10`。小票不会是 2070 年的东西，按 POSIX 惯例补世纪。
        # 这条假设写在明面上，不是偷偷塞进来的。
        head = int(parts[0])
        parts[0] = f"20{head:02d}" if head < 70 else f"19{head:02d}"
        s = "-".join(parts)
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return ""
    return s


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

    **抄完自查一次，对不上就带着错处重问。** 见 `_looks_consistent`。
    """
    photo = input_data.get("photo")
    if not isinstance(photo, str) or not photo.strip():
        raise ValueError("receipt_photo.image is required")

    image = _as_viewable_image(photo)
    raw = complete_json(
        _READ_PROMPT,
        images=[image],
        thinking="disabled",
        max_tokens=8000,
    )
    if not isinstance(raw, dict):
        raise ValueError("llm did not return a receipt object")

    # 第二遍只在真出问题时才花钱。抄一次就对（而且齐）的票（绝大多数）
    # 零额外开销。两种触发共用一次调用：
    #   - 行算术对不上（抄错）
    #   - 必填字段整行读空（漏抄）
    # 只查前一种的话，最常见的漏抄反而没人管——见 _missing_required_fields。
    suspect = _looks_inconsistent(raw)
    missing = _missing_required_fields(raw)
    if suspect or missing:
        raw = _reread_once(image, raw, suspect, missing)
    # 套一层 parsed：下游 op 的输入 = 上游 op 的输出，
    # 不包装的话 verify 收到的是 task input（只有 photo），parsed 取不到。
    return {"parsed": raw}


# ============================================================
# 抄完自查 → 对不上就重问一次
# ============================================================
#
# 为什么需要：抽取腿是整套里唯一的模型环节，而它**从来不回头看**。
# 实测同一个错连犯三次，全是「抓错了那个最显眼的数字」：
#   1. 「500ml*2」里的 2 当成数量（规格不是数量）
#   2. 小票的「合计」当成某一行��单价
#   3. 「支付宝优惠 -0.18」当成税额
# 三次都不是模型不会认字，是**它没有把这一行和别的数字对一遍**。
#
# 为什么不用 prompt 里写「请检查」：写过了，它还是自信地给错答案
# （读错单价那次的 low_confidence_fields 里压根没提单价）。
# 模型对自己的输出没有可靠的自我批评，提示词换不来第二次注意力。
# 把「哪些行对不上」算出来再摊给它看，才是真的多给了它一次注意力。
#
# 边界（照实说，别把它当万能药）：
#   - 第二遍**仍可能是在凑数**。看到「1×8.4≠4.5」之后把 amount 改成
#     8.4 也是一种「让它对上」的办法。区别在于金额改动会被 sum_arithmetic
#     逮到（Σ行金额会对不上小计），但原理上这一层挡不住所有作弊。
#   - 只查得出**自相矛盾**，查不出「一致地错」（单价金额一起认错）。
#   - 最多问一次，不循环。再错就交给验收判据红——那是它的活。


def _looks_inconsistent(parsed: dict[str, Any]) -> list[str]:
    """这份抄件里有哪些行自相矛盾（纯机械，零 LLM）

    只问一个事：**每一行的 数量 × 原价 × 折扣，跟这一行的金额对不对得上。**
    不查别的——查多了会把「票型本来就这样」误判成抄错（比如没单价栏的
    结账单、折后商品），那就是把判据的坑又挖一遍。
    """
    items = parsed.get("items")
    if not isinstance(items, list):
        return []
    bad = []
    for i, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            bad.append(i)
            continue
        qty, price = _num(it.get("qty")), _num(it.get("unit_price"))
        amt = _num(it.get("amount"))
        if None in (qty, price, amt):
            bad.append(i)
            continue
        if not _line_amount_ok(qty, price, _discount_multiplier(it.get("discount_rate")), amt):
            bad.append(i)
    return bad


# 抄空了就是错的字段。merchant_name / receipt_no **不在这里**——
# 那两个是「至少有一个」（票上可能只印其中之一），缺单个不算缺。
_REQUIRED_FIELDS = ("receipt_date",)


def _missing_required_fields(parsed: dict[str, Any]) -> list[str]:
    """哪些必填字段是**整行读空**的（纯机械，零 LLM）

    为什么查这个：实测抽取腿把 `2026.10.10 20:36` 整行塞进了 receipt_no、
    让 receipt_date 空着——票上明明印着日期。行算术**查不出这种错**
    （三行金额全对），但 `date_wellformed` 会红，而抽取腿毫无反应：
    它从来不回头看自己抄的字段齐不齐，只能等验收闸拦。

    这跟「行算术对不上就重问」是同一件事的两个面：**算错了要重问，
    没抄到也要重问**。只查算术，等于只认「抄错」不认「漏抄」，
    于是最常见的漏抄（日期塞错字段 → 日期空着）反而没人管。

    边界（照实说）：这里只认「空」和「不空」，**不判断非空的值对不对**。
    单号填成 `202610101 010 20:36` 这种「有值但是日期」的情形，
    机器判不了——单号格式本来就没有统一标准。这是已声明的盲区。
    """
    out = []
    for name in _REQUIRED_FIELDS:
        value = parsed.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            out.append(name)
    return out


def _reread_problem(parsed: dict[str, Any], bad: list[int], missing: list[str]) -> str:
    """把「哪儿不对」摊给模型看的那一段（纯拼字符串）"""
    blocks: list[str] = []
    if bad:
        items = parsed.get("items") or []
        shown = [
            {"行号": i,
             "品名": (items[i - 1] or {}).get("name") if isinstance(items[i - 1], dict) else None,
             "数量": (items[i - 1] or {}).get("qty") if isinstance(items[i - 1], dict) else None,
             "单价": (items[i - 1] or {}).get("unit_price") if isinstance(items[i - 1], dict) else None,
             "金额": (items[i - 1] or {}).get("amount") if isinstance(items[i - 1], dict) else None}
            for i in bad if 1 <= i <= len(items)
        ]
        blocks.append("【对不上的行】（你上一遍抄的）\n"
                      + json.dumps(shown, ensure_ascii=False))
    if missing:
        blocks.append("【整行读空的字段】\n"
                      + json.dumps(list(missing), ensure_ascii=False)
                      + "\n这些字段你上一遍填了 null 或空串，但票上印着。")
    return "\n\n".join(blocks)


def _reread_once(image: str, parsed: dict[str, Any],
                 bad: list[int], missing: list[str]) -> dict[str, Any]:
    """把对不上的行 + 读空的字段一起摊回去，让模型重新看一眼图

    合并策略：**只接受第二遍对这几处的改写**，其余字段保持第一遍。
    全量替换会让第二遍顺手改掉它本来抄对的东西。

    两件事**共用一次调用**——「最多问一次」这条不变。拆成两次问，
    代价翻倍，还会让模型拿到两份互相矛盾的上下文。
    """
    items = parsed.get("items") or []
    prompt = _REREAD_PROMPT.format(
        problem=_reread_problem(parsed, bad, missing) or "（没有具体问题）"
    )
    # 第二遍挂了就让整条履约中止，不退回第一遍。
    #
    # 走到这里的前提是**第一遍自己都说不通**（算术对不上 / 必填字段空着）。
    # 这种情况下退回一份我们明知有问题、又没能复核的产出，就是静默降级
    # ——看起来成功了，实际上交出去的是一份自己都说不清的账。中止是吵，
    # 但吵得对。
    fixed = complete_json(prompt, images=[image], thinking="disabled", max_tokens=4000)
    if not isinstance(fixed, dict):
        raise ValueError("llm rereread did not return an object")

    merged = dict(parsed)
    new_items = list(items)
    by_line = {
        int(x.get("行号")): x for x in (fixed.get("修正") or [])
        if isinstance(x, dict) and str(x.get("行号", "")).isdigit()
    }
    for i in bad:
        patch = by_line.get(i)
        if not patch or not isinstance(new_items[i - 1], dict):
            continue
        row = dict(new_items[i - 1])
        for src, dst in (("数量", "qty"), ("单价", "unit_price"),
                         ("金额", "amount"), ("折扣", "discount_rate")):
            if src in patch:
                row[dst] = patch[src]
        new_items[i - 1] = row
    merged["items"] = new_items
    # 补字段：**只接受读出来的非空值**。第二遍还是空就保持空——
    # 让 date_wellformed 去红它。硬塞一个占位值才是造假信号。
    patch_fields = fixed.get("补字段")
    if isinstance(patch_fields, dict):
        for name in missing:
            value = patch_fields.get(name)
            if isinstance(value, str) and value.strip():
                merged[name] = value.strip()
            elif value is not None and not isinstance(value, (list, dict)):
                merged[name] = value
    if fixed.get("low_confidence_fields"):
        merged["low_confidence_fields"] = list(
            dict.fromkeys(list(parsed.get("low_confidence_fields") or [])
                          + list(fixed["low_confidence_fields"]))
        )
    return merged


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
        subtotal is not None and total is not None and has_identity
        # 「有商品行」得是真有行：items 里塞一堆字符串也是「非空」，
        # 但那不叫解析出了小票。
        and any(isinstance(it, dict) for it in items)
    )

    # 逐行：数量 × 原价 × 折扣 ≈ 行金额（容差见 _line_amount_ok）
    #
    # 三态，不是两态。餐馆结账单只印「品项/数量/金额」，压根没有单价栏——
    # 这时这条判据**用不上**，既不该说它过，也不该说它错。
    #
    # 怎么区分「票上没这一栏」和「模型没抄出来」：
    #   一行都没有单价 → 大概率票上没这栏（超市 POS、餐馆结账单都这样）
    #   有的有、有的没有 → 票上有这栏，是模型漏抄 → 真失败
    # 这是启发式，不是判据。已声明的盲区：一张**确实印了单价栏**、
    # 而模型在每一行都漏抄的票，会被误读成「票上没单价」而放行。
    # 跟「字认对没有」一样，机器判不了就明说，不假装能判。
    line_bad = []
    line_sum = 0.0
    row_count = 0          # items 里一共有几行（不管这行长不像样）
    struct_rows = 0        # 其中读成结构化对象的有几行
    rows_with_price = 0    # 其中读到原价的有几行
    for i, it in enumerate(items, start=1):
        row_count += 1
        if not isinstance(it, dict):
            line_bad.append(i)
            continue
        struct_rows += 1
        qty, price, amt = _num(it.get("qty")), _num(it.get("unit_price")), _num(it.get("amount"))
        if price is not None:
            rows_with_price += 1
        amt_ok = (
            qty is not None and price is not None and amt is not None
            and _line_amount_ok(qty, price, _discount_multiplier(it.get("discount_rate")), amt)
        )
        if not amt_ok:
            line_bad.append(i)
        if amt is not None:
            line_sum += amt

    if row_count == 0:
        # 压根没行可验。（这条单独看可以放行——field_completeness
        # 会因为 items 为空而转红，整张票照样被拦。）
        line_arithmetic = NOT_APPLICABLE
    elif struct_rows > 0 and rows_with_price == 0:
        # 读出了结构化的行、但一行单价都没有 → 票上大概率没这一栏。
        # **前提是 struct_rows > 0**：一堆读不成对象的行同样「没有单价」，
        # 但那是模型抄砸了，不是票上没这栏。不加这个前提，
        # 垃圾行会被当成「不适用」白放行——比算错还难查。
        line_arithmetic = NOT_APPLICABLE
    else:
        line_arithmetic = not line_bad

    # Σ行金额 == 小计
    sum_arithmetic = (
        subtotal is not None and abs(line_sum - subtotal) <= 0.01
    )

    # 小计 + 税 − **另减的**优惠 = 实付
    #
    # 优惠这一项是零售 POS 小票的标配（支付宝优惠 / 满减 / 抹零），而 POS 小票
    # **没有税**。以前公式只写「小计 + 税 = 实付」，票上那个优惠数就无处安放，
    # 模型只能给 null，于是每张打过折的票都必然转红。
    # 两种票据都在这套公式里：POS 小票 tax 缺省 0、discount 有值；
    # 增值税发票 tax 有值、discount 缺省 0。
    #
    # **但不是每张票的优惠都是从合计里减掉的**（同一个病根的第五次）。
    # 实测六贤记烘焙单：
    #     奶皮鞭子   原价 8.80   实收单价 7.50   数量 2   小计 15.00
    #     合计:39.70(2.60)   折让金额:2.60   付款 39.70
    # 2.60 = 2 × (8.80 − 7.50) —— 折让是**奶皮鞭子那一行的差价**，
    # 而 39.70 = 15.90 + 8.80 + 15.00 已经等于 Σ行金额，也就是说
    # **折让含在合计里**。再减一次就成 37.10，跟实付对不上。
    #
    # 括号里那个数是票面印着的，读它就行，不用推。
    # 减的应该是「另减的那部分」= discount − included_discount。
    discount = _num(parsed.get("discount"))
    included = _num(parsed.get("included_discount"))
    # 折让不可能比「折让金额」还多：included > discount 一定是读错了
    # （MUJI 的「实物总额(9%)」是税率不是金额，模型可能把它填进来）。
    # 这种就当没含，照常减——宁可让票红，也不要拿一个错数把公式凑平。
    if included is not None and (discount is None or included > discount):
        included = None
    deducted = (discount or 0.0) - (included or 0.0)
    total_arithmetic = (
        subtotal is not None and total is not None
        and abs(subtotal + (tax or 0.0) - deducted - total) <= 0.01
    )

    # 日期：**先机械归一再判真假**（`_normalize_date` 写了为什么）
    #
    # 归一跟判真值是两件事：归一只放宽**写法**
    # （`2026.10.10` 跟 `2026-10-10` 是同一天，不是抄错），
    # 日期本身合不合法仍由 strptime 判——`2026.13.45` 照样红。
    date_norm = _normalize_date(parsed.get("receipt_date"))
    date_ok = bool(date_norm)
    time_raw = parsed.get("receipt_time") or ""
    time_ok = time_raw in ("", None) or bool(_HH_MM.match(str(time_raw)))
    date_wellformed = date_ok and time_ok

    return {
        # 归一后的日期带下去，看板上跟产出里统一是 ISO。
        # 不带的话同一个日期在两个地方长得不一样，对账的人得自己比。
        "parsed": {**parsed, "receipt_date": date_norm},
        "field_completeness": field_completeness,
        "line_arithmetic": line_arithmetic,
        "sum_arithmetic": sum_arithmetic,
        "total_arithmetic": total_arithmetic,
        "date_wellformed": date_wellformed and time_ok,
        # 总口径：没有任何一条判据是 False。
        #
        # 写成 `is not False` 而不是 and 链，不是为了防类型污染——
        # （实测过：and 短路返回第一个假值、否则最后一个操作数，
        #   字符串在链中间漏不出去，旧写法其实也没真出错。）
        # 真正的原因：让「n/a 不拖后腿」这件事变成显式声明。
        # 现在它靠的是 bool("n/a") is True 这个巧合——哪天有人调整判据顺序、
        # 把 line_arithmetic 挪到链尾，它就会变成字符串悄悄漏出去。
        "totals_match": (
            field_completeness is not False
            and line_arithmetic is not False
            and sum_arithmetic is not False
            and total_arithmetic is not False
            and date_wellformed
            and time_ok
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
            # 折数原样带出（没折扣是 None，不是 0 也不是 10）。
            # 带不出去就等于把「这一行打过折」这个事实从产出里抹了——
            # 人工对账时看到原价 498、行金额 290 会一头雾水，
            # 而这恰恰是票上明明白白印着的。
            "discount_rate": _num(it.get("discount_rate")),
        })

    return {
        "merchant_name": parsed.get("merchant_name") or "",
        "receipt_no": parsed.get("receipt_no") or "",
        "receipt_date": parsed.get("receipt_date") or "",
        "receipt_time": parsed.get("receipt_time") or "",
        "items": normalized_items,
        # exception `items_count == 0` 读的量。以前规则指向一个产出里
        # 没有的字段，所以「没读出商品行就不交付」这条从来没生效过。
        "items_count": len(normalized_items),
        "subtotal": _num(parsed.get("subtotal")),
        "tax": _num(parsed.get("tax")),
        "discount": _num(parsed.get("discount")),
        # 合计里已含的优惠。带不出去，看板上就还是「优惠 2.60」，
        # 而实付 39.70 跟 39.70 − 2.60 对不上——用户会以为算错了。
        "included_discount": _num(parsed.get("included_discount")),
        "total": _num(parsed.get("total")),
        "payment_method": parsed.get("payment_method") or "",
        "field_completeness": bool(v.get("field_completeness")),
        # **别用 bool()**：bool("n/a") 是 True，会把「这张票用不上这条判据」
        # 变成「这条判据通过了」——正是这套判据存在的意义的反面。
        # 三态原样带过去。
        "line_arithmetic": _keep_state(v.get("line_arithmetic")),
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