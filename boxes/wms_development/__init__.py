"""boxes.wms_development - WMS Development Box

WMS 开发盒——**按设计包改代码**：读方案 → 定位源码 → 改 → 编译 → 制品。

**成熟度：live**。这条腿是真的：它真的起隔离工作区（git worktree）、
真的让模型改代码、真的编译、真的跑仓库自带的测试套件。

判据的答案全部来自**外部神谕**（编译器 / 测试套件 / git），
没有一条来自这只盒子自述——这是「按方案生成代码」这条路能站住的前提。

**工作区必须隔离**：开发盒会真的动文件，动的是 WMS 主仓的话，
一次跑失败的履约就把别人的工作现场弄脏了。所以动的是 worktree，
提交落回主仓的一个分支（`beeos/dev-<sha>`），交付物追得回去。
"""
from __future__ import annotations

from boxes.wms_development.definition import WMS_DEVELOPMENT_BOX, get_definition
from boxes.wms_development.runner import RUNNER, WMS_DEVELOPMENT_HANDLERS
from boxes.wms_development.beeline import WMS_DEVELOPMENT_BEELINE, get_beeline

__all__ = ["WMS_DEVELOPMENT_BOX", "get_definition", "RUNNER", "WMS_DEVELOPMENT_HANDLERS", "WMS_DEVELOPMENT_BEELINE", "get_beeline"]
