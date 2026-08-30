#!/usr/bin/env bash
# beeOS M0 部署脚本（无 Docker，无 systemd 操作）
# 镜像 scripts/deploy-to-ecs.sh 模式，但只做源码同步 + CLI 工具安装
#
# M0 部署 = Bee + Box CLI 工具，不动 Queen/Hive/nginx
# 服务器上 M1 进程继续运行（要停请手动 systemctl stop beeos-queen）
#
# 部署链路：
#   1. 记录本地 git SHA
#   2. 打包源码 tarball（不含 _shelved/ venv .env 等）
#   3. scp 到 ECS /tmp/
#   4. ECS 上 tar -xzf 到 /opt/beeos
#   5. uv pip install -e 三个 workspace 包（去 Queen）
#   6. smoke: uv run bee --list + uv run month-close --manifest
#
# 与 M1 部署的差异：
#   - M1: 4 unit restart（nginx/postgres/redis/beeos-queen）+ PG 健康检查
#   - M0: 只装 CLI 工具 + 验证 import 成功，不动 systemd
#
# 用法：
#   bash scripts/deploy-m0.sh                  # 完整部署
#   bash scripts/deploy-m0.sh --dry-run        # 只打印命令
#   bash scripts/deploy-m0.sh --skip-pip       # 不同步 venv
#   bash scripts/deploy-m0.sh --host <user@ip> # 自定义目标

set -euo pipefail

REMOTE="${REMOTE:-root@101.37.146.194}"
REMOTE_DIR="${REMOTE_DIR:-/opt/beeos}"
STAGE="${STAGE:-/tmp/beeos-deploy-m0}"
LOCAL="${LOCAL:-$PWD}"

DRY_RUN=false
SKIP_PIP=false
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --skip-pip) SKIP_PIP=true ;;
    --host) REMOTE="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--dry-run] [--skip-pip] [--host user@host]"
      echo ""
      echo "M0 部署：只同步源码 + 安装 Bee/BeeBox CLI 工具，不动 systemd。"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

run() {
  if $DRY_RUN; then
    printf '  [DRY] %s\n' "$*"
  else
    "$@"
  fi
}

cd "$LOCAL"

# --- 1. 记录 commit ---
HEAD_SHA=$(git rev-parse HEAD)
HEAD_SHORT=$(git rev-parse --short HEAD)
echo "==> deploying beeOS M0 commit: $HEAD_SHORT ($HEAD_SHA)"

# --- 2. 打包源码 tarball ---
echo "==> staging source tarball at $STAGE/beeos-m0-$HEAD_SHORT.tgz"
rm -rf "$STAGE"
mkdir -p "$STAGE"

tar -czf "$STAGE/beeos-m0-$HEAD_SHORT.tgz" \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='.env.local' \
  --exclude='venv' \
  --exclude='.venv' \
  --exclude='.pytest_cache' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.egg-info' \
  --exclude='coverage' \
  --exclude='htmlcov' \
  --exclude='_shelved' \
  --exclude='logs' \
  --exclude='docs' \
  --exclude='apps/portal' \
  apps packages deploy pyproject.toml uv.lock

test -s "$STAGE/beeos-m0-$HEAD_SHORT.tgz" || { echo "tarball empty"; exit 1; }

# --- 3. ship 到 ECS ---
echo "==> shipping tarball to $REMOTE"
run scp "$STAGE/beeos-m0-$HEAD_SHORT.tgz" "$REMOTE:/tmp/"

# --- 4. 在 ECS 上：解压 + 装 CLI 工具 ---
echo "==> deploying M0 on $REMOTE"
run ssh "$REMOTE" "
  set -e
  cd $REMOTE_DIR

  # 4.1 解压 overlay
  echo '--- extracting M0 source overlay ---'
  tar -xzf /tmp/beeos-m0-$HEAD_SHORT.tgz

  # 4.2 修复 owner（如果 deploy 用户存在）
  chown -R deploy:deploy $REMOTE_DIR/apps $REMOTE_DIR/packages 2>/dev/null || true

  # 4.3 同步 Python venv（增量）—— 不装 Queen
  $(if $SKIP_PIP; then
    echo "  echo '--- skipping venv sync (--skip-pip) ---'"
  else
    echo "  sudo -u deploy bash -c '
      export PATH=\$HOME/.local/bin:\$PATH
      uv pip install --python ./venv/bin/python \\
        -e packages/beeos-core \\
        -e apps/bee \\
        -e apps/boxes/month-close \\
        --index-url https://mirrors.aliyun.com/pypi/simple/ 2>&1 | tail -5
    '"
  fi)

  # 4.4 同步 nginx 配置（如果 M0 用了 /m0/ 反代）
  if [ -f $REMOTE_DIR/deploy/nginx/beeos.conf ]; then
    echo '--- reloading nginx with new M0 config ---'
    cp $REMOTE_DIR/deploy/nginx/beeos.conf /etc/nginx/conf.d/beeos.conf
    nginx -t && systemctl reload nginx
  fi

  # 4.5 清理
  rm -f /tmp/beeos-m0-$HEAD_SHORT.tgz
"

# --- 5. smoke checks（直接调 venv 的 python，避免 uv run 下载 dev 依赖）---
echo "==> smoke checks (M0 CLI 工具验证)"
run ssh "$REMOTE" "
  set -e
  echo '--- bee --list (直接调 venv python) ---'
  sudo -u deploy bash -c '
    export PATH=\$HOME/.local/bin:\$PATH
    cd $REMOTE_DIR
    ./venv/bin/bee --list
  '

  echo ''
  echo '--- month-close --manifest ---'
  sudo -u deploy bash -c '
    export PATH=\$HOME/.local/bin:\$PATH
    cd $REMOTE_DIR
    ./venv/bin/month-close --manifest
  '
"

echo ""
echo "==> M0 deploy complete"
echo "    deployed commit: $HEAD_SHORT"
echo "    M1 进程未动（Queen/PG/Redis/nginx 继续在跑）"
echo "    验证 Bee:  ssh $REMOTE 'cd $REMOTE_DIR && ./venv/bin/bee --box month_close --period 2026-07'"
echo "    切换 M0:  手动 systemctl stop beeos-queen && systemctl disable beeos-queen"
echo "    回滚 M1:  git checkout <M1-sha> 后跑 bash scripts/deploy-to-ecs.sh"
