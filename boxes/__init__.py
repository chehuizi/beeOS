"""boxes - 业务履约盒子产品"""

# 业务化枚举（跨盒子共享）
from core.models import (
    ExceptionType,
    Severity,
    CustomerTier,
    ResolutionType,
)

# 已实现的盒子
from boxes.inventory_shortage import get_definition as get_inventory_shortage_definition
from boxes.modeling import get_definition as get_business_modeling_definition