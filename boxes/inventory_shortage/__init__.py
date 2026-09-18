"""boxes.inventory_shortage - 库存不足订单业务履约盒

第一只业务履约盒子——订单发生库存不足时，按政策在 SLA 内给客户一个
符合政策的可验证结果（替代仓 / 调拨 / 补货 / 退款 / 人工）。

按 [docs/examples/order-exception-box.md] 实现，把 §2 YAML definition
翻译成 pydantic 模型。

业务语义是真的（policy / contract / acceptance 完整定义）；
基础设施是假的（runtime / executor / worker 都是 mock，下一阶段实现）。
"""

from boxes.inventory_shortage.definition import get_definition, ORDER_EXCEPTION_BOX

__all__ = ["get_definition", "ORDER_EXCEPTION_BOX"]