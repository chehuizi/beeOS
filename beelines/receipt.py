"""
beelines.receipt - 小票识别盒的履约流水线

三步，每步一个工位：
1. read_receipt     看图抄字（唯一真调 LLM 的一步）
2. verify_receipt   算术与格式自洽（纯机械）
3. package_receipt  合并成产出包（纯机械）

跟其他 beeline 的区别只有一处，但很关键：**LLM 只在第 1 步**。
校验和打包都不再碰模型，所以「模型为了好看把数字凑平」这条路是堵死的。
"""

from __future__ import annotations

from core.beeline_models import Beeline, Bee, NextRef, Operation


RECEIPT_CAPTURE_BEELINE = Beeline(
    id="beeline_receipt_capture_v1",
    version=1,
    description="看图 → 机械校验 → 打包",
    operations=[
        Operation(
            op_id="read_receipt",
            type="read_receipt",
            input_from="external",
            input="schema_receipt_photo",
            output="schema_receipt_parsed",
            next=[NextRef(op_id="verify_receipt")],
            bee=Bee(type="receipt_reader"),
        ),
        Operation(
            op_id="verify_receipt",
            type="verify_receipt",
            input_from="read_receipt",
            input="schema_receipt_parsed",
            output="schema_receipt_parsed",
            next=[NextRef(op_id="package_receipt")],
            bee=Bee(type="arithmetic_checker"),
        ),
        Operation(
            op_id="package_receipt",
            type="package_receipt",
            input_from="verify_receipt",
            input="schema_receipt_parsed",
            output="schema_receipt_parsed",
            next=[],
            bee=Bee(type="receipt_packager"),
        ),
    ],
)


def get_beeline() -> Beeline:
    return RECEIPT_CAPTURE_BEELINE