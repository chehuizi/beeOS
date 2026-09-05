"""beeOS Kernel - 任务接收 / BOM 拆解 / MES 执行。

按 beeOS 架构：
- Kernel = 内核（任务调度 + BOM 缓存 + MES 执行）
- Workshop = 中央 BOM 库（boms/*.yaml）
- BeeBox = 工具运行时（按 workspace.bee_box_ref 加载）
- Hive = 审计/历史日志（V1+ 接入）

5 大组件：
  ① Task Receiver   验证入队
  ② Workspace Router  找目标车间
  ③ BOM Cache     查 BOM 蓝图
  ④ Bee Planner    缺图时规划（V1+ 启用 LLM）
  ⑤ MES Executor   按 BOM 调度 BeeBox 工具
"""

__version__ = "0.1.0"
