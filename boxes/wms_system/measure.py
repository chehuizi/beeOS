"""boxes.wms_system.measure - WMS System Box 的实测指标采集

**空表，而且这是刻意的。** 骨架盒不声明任何指标（没有产出就没有可测
的量），所以没有采集器可挂。

表本身仍然要存在：`tests/test_metrics.py` 要求每个注册盒子都在
METRIC_COMPUTERS 里有位置。缺这张表不是「还没写」，是**注册不完整**。
"""
from __future__ import annotations

from typing import Any

from core import metrics as core_metrics

Computer = core_metrics.Computer

COMPUTERS: dict[str, Computer] = {}
