"""以 git 内容为准，同步到部署目录。

为什么需要这个：`git archive | tar xzf -` **只解压，不删除**。
所以每一个被 git 删掉的文件，都会永远留在服务器上。

实测踩过：把 9 条 beeline 从 `beelines/` 搬进 `boxes/<box>/` 之后，
服务器上 `beelines/` 整个包还在——两份 beeline 并存，
旧的还带着过时的 op 链。谁 import 到哪一份，取决于 import 路径的写法，
而没有任何东西会报错。

**两份真相并存，且不会自己暴露**——这是静默降级里最难查的一种。

所以部署必须做减法：目标目录里凡是 git 里没有的，一律删掉
（运行时数据目录除外：.venv / logs / data）。
"""

from __future__ import annotations

import filecmp
import os
import shutil
import sys
from pathlib import Path

# 这些目录是运行时资产，不归 git 管，不能删
PRESERVE_DIRS = {".venv", "logs", "data", "__pycache__", ".git"}


def should_skip(path: Path, target: Path) -> bool:
    rel = path.relative_to(target)
    return bool(PRESERVE_DIRS & set(rel.parts))


def main() -> int:
    stage, target = Path(sys.argv[1]), Path(sys.argv[2])

    # 目标里多出来的文件 = git 里已经没有的
    stale: list[Path] = []
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if should_skip(path, target):
            continue
        rel = path.relative_to(target)
        if not (stage / rel).exists():
            stale.append(path)

    for path in stale:
        path.unlink()
        print(f"  删（git 里已无）: {path.relative_to(target)}")

    # 清掉因此变空的目录
    for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not should_skip(path, target):
            try:
                next(path.iterdir())
            except StopIteration:
                path.rmdir()
                print(f"  删空目录: {path.relative_to(target)}")

    # 覆盖写入
    changed = 0
    for src in stage.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(stage)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and filecmp.cmp(src, dst, shallow=False):
            continue
        shutil.copy2(src, dst)
        changed += 1
    print(f"  更新 {changed} 个文件，删除 {len(stale)} 个过期文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
