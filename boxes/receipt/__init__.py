"""boxes.receipt - 小票识别履约盒

输入一张收银小票照片，输出结构化小票 + 6 项机械校验证据。
模型只负责看图抄字，算术一律由盒子自己算——
「模型把数字凑平」这条路是堵死的。
"""

from boxes.receipt.definition import RECEIPT_CAPTURE_BOX, get_definition

__all__ = ["RECEIPT_CAPTURE_BOX", "get_definition"]
