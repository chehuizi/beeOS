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
from boxes.wms_development import get_definition as get_wms_development_definition
from boxes.wms_ops import get_definition as get_wms_ops_definition
from boxes.wms_system import get_definition as get_wms_system_definition
from boxes.wms_testing import get_definition as get_wms_testing_definition
from boxes.wms_design import get_definition as get_wms_design_definition
from boxes.receipt import get_definition as get_receipt_capture_definition
