#!/usr/bin/env bash
#
# scripts/deploy.sh —— 把当前 HEAD 部署到服务器
#
# 用法：
#   scripts/deploy.sh                      # 默认 root@101.37.146.194
#   scripts/deploy.sh user@host            # 换目标
#   scripts/deploy.sh user@host /some/dir  # 连部署目录一起换
#
# ## 为什么不是 `git archive | ssh host 'tar xzf -'`
#
# 那个方式**只解压，不删除**。每一个被 git 删掉的文件都会永远留在服务器上。
#
# 实测踩过：把 9 条 beeline 从 `beelines/` 搬进 `boxes/<box>/` 之后，
# 服务器上 `beelines/` 整个包还在——两份 beeline 并存，旧的还带着过时的
# op 链。import 到哪一份取决于写法，而**没有任何东西会报错**。
#
# 所以这里分两步：先把 tarball 传上去（scp），再用 sync 脚本以 git 内容
# 为准做同步（更新 + 删除过期文件）。运行时资产（.venv / logs / data）不动。
#
# ## 为什么不用 rsync
#
# 服务器上没装。sync 逻辑用 python3 实现，服务器自带。

set -euo pipefail

HOST="${1:-root@101.37.146.194}"
REMOTE_DIR="${2:-/opt/beeos-kanban}"
SERVICE="${BEEOS_SERVICE:-beeos-kanban.service}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 未提交的改动会让「部署的」和「你以为的」不是同一份东西。
# `git diff` 看不见 untracked 文件，所以要单独查——
# 漏掉这一条的后果是：以为部署了新脚本，其实发上去的是旧版。
if ! git diff --quiet || ! git diff --cached --quiet \
   || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "⚠️  工作区有未提交的改动（含未跟踪文件）。部署的是 HEAD，不是工作区——"
  echo "    确认这是你要的，还是先 commit。"
fi

COMMIT="$(git rev-parse --short HEAD)"
echo "→ 部署 $COMMIT 到 $HOST:$REMOTE_DIR"

TARBALL="$(mktemp -t beeos-deploy-XXXXXX.tar.gz)"
trap 'rm -f "$TARBALL"' EXIT
git archive --format=tar.gz HEAD > "$TARBALL"

echo "→ 传输 $(du -h "$TARBALL" | cut -f1)"
scp -q -o BatchMode=yes "$TARBALL" "$HOST:/tmp/beeos-release.tar.gz"

echo "→ 同步（更新 + 删除 git 里已无的文件）"
ssh -o BatchMode=yes "$HOST" "bash -s" -- "$REMOTE_DIR" "$SERVICE" <<'REMOTE_SCRIPT'
set -euo pipefail

REMOTE_DIR="$1"
SERVICE="$2"

STAGE="$(mktemp -d /tmp/beeos-stage-XXXXXX)"
trap 'rm -rf "$STAGE" /tmp/beeos-release.tar.gz' EXIT
tar xzf /tmp/beeos-release.tar.gz -C "$STAGE"

# 用应用自己的 venv python：服务器的系统 python3 是 3.6，
# 而 sync 脚本要跑的目录里有 3.12 的包——两者不是一回事。
PY_BIN="$REMOTE_DIR/.venv/bin/python"
[ -x "$PY_BIN" ] || PY_BIN=python3

"$PY_BIN" "$STAGE/scripts/_sync_release.py" "$STAGE" "$REMOTE_DIR"

chown -R deploy:deploy "$REMOTE_DIR"
find "$REMOTE_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

systemctl restart "$SERVICE"
sleep 3
systemctl is-active "$SERVICE"
REMOTE_SCRIPT

echo "✓ 已部署 $COMMIT"
echo
echo "线上自检："
ssh -o BatchMode=yes "$HOST" "curl -s -o /dev/null -w '  看板 http=%{http_code}\n' http://127.0.0.1:8765/api/data" 2>/dev/null || true
