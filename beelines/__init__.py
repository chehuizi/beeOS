"""beelines - 履约作业路线（procedure 实现）包入口

beeline 独立维护，不归 beeBox definition 管；
definition 通过 task[].beeline_id + beeline_version 引用。
"""

from beelines.inventory_shortage import INVENTORY_SHORTAGE_BEELINE, get_beeline as get_inventory_shortage_beeline
from beelines.modeling import WMS_DESIGN_BEELINE, get_beeline as get_modeling_beeline
from beelines.receipt import RECEIPT_CAPTURE_BEELINE, get_beeline as get_receipt_capture_beeline
from beelines.wms_analysis import WMS_ANALYSIS_BEELINE, get_beeline as get_wms_analysis_beeline
from beelines.wms_development import WMS_DEVELOPMENT_BEELINE, get_beeline as get_wms_development_beeline
from beelines.wms_ops import WMS_OPS_BEELINE, get_beeline as get_wms_ops_beeline
from beelines.wms_system import WMS_SYSTEM_BEELINE, get_beeline as get_wms_system_beeline
from beelines.wms_testing import WMS_TESTING_BEELINE, get_beeline as get_wms_testing_beeline
from beelines.requirement_capture import WMS_REQUIREMENT_BEELINE, get_beeline as get_requirement_capture_beeline

__all__ = [
    "INVENTORY_SHORTAGE_BEELINE",
    "get_inventory_shortage_beeline",
    "WMS_DESIGN_BEELINE",
    "get_modeling_beeline",
    "WMS_REQUIREMENT_BEELINE",
    "get_requirement_capture_beeline",
    "WMS_SYSTEM_BEELINE", "get_wms_system_beeline",
    "WMS_DEVELOPMENT_BEELINE", "get_wms_development_beeline",
    "WMS_TESTING_BEELINE", "get_wms_testing_beeline",
    "WMS_OPS_BEELINE", "get_wms_ops_beeline",
    "WMS_ANALYSIS_BEELINE", "get_wms_analysis_beeline",
    "RECEIPT_CAPTURE_BEELINE",
    "get_receipt_capture_beeline",
]