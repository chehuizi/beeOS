"""tests.fixtures.sample_box - 合成夹具盒

不是产品，是**测试夹具**：给运行时 / 看板的机制测试提供一只可控的盒子。
不进 `BOX_REGISTRY`、不进打包清单、不出现在看板上。
"""

from __future__ import annotations

from tests.fixtures.sample_box.beeline import SAMPLE_BEELINE, get_beeline
from tests.fixtures.sample_box.definition import SAMPLE_BOX, get_definition

__all__ = ["SAMPLE_BOX", "SAMPLE_BEELINE", "get_definition", "get_beeline"]
