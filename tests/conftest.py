"""beeOS M0 测试 conftest。

M0 阶段不需要 SQLite in-memory DB 或 Queen 客户端 fixture —— Bee 和 Box 都是纯异步函数。
V1+ 恢复 Queen/Hive 后，这里再加 httpx / sqlalchemy fixture。
"""

import sys
from pathlib import Path

# 把 src/ 加到 sys.path，让 `from bee.xxx` / `from month_close.xxx` 能 import
ROOT = Path(__file__).resolve().parent.parent
for pkg in ["apps/bee/src", "apps/boxes/month-close/src", "packages/beeos-core/src"]:
    sys.path.insert(0, str(ROOT / pkg))
