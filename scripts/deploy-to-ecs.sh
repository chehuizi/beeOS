#!/usr/bin/env bash
# beeOS ECS 部署脚本（无 Docker，原生 systemd）
# 镜像 domain-box/scripts/deploy-to-ecs.sh 模式，但适配 4-unit 现状
#
# 部署链路：
#   1. 本地 git SHA 记录
#   2. 打包源码 tarball（不含 venv / .env / 大文件）
#   3. scp 到 ECS /tmp/
#   4. ECS 上 tar -xzf 到 /opt/beeos
#   5. uv pip sync venv（增量同步 Python 依赖）
#   6. systemctl daemon-reload + restart beeos-queen
#   7. 4-unit smoke check（systemctl is-active / curl /health / redis-cli ping / psql SELECT 1）
#
# 与 domain-box 的差异：
#   - domain-box: 单 Next.js 进程 + 裸跑
#   - beeOS: 4 个 native systemd unit（nginx / postgresql / redis / beeos-queen）+ uv pip sync
#
# 用法：
#   bash scripts/deploy-to-ecs.sh                  # 完整部署
#   bash scripts/deploy-to-ecs.sh --dry-run        # 只打印命令
#   bash scripts/deploy-to-ecs.sh --skip-pip       # 不同步 venv
#   bash scripts/deploy-to-ecs.sh --host <user@ip> # 自定义目标
#
# 部署前清单：
#   1. 提交所有要发的改动（脚本不自动 commit）
#   2. 本地 ssh 能免密登录 ECS（~/.ssh/config 或 key in known_hosts）
#   3. ECS 上 /opt/beeos/.env 已就位（脚本不传 .env，全部 host-resident）
#   4. ECS 上 4 个 systemd unit 的依赖服务（postgresql / redis）已就绪
#   5. 如果是首次部署，先按 docs/DEPLOY.md §一次性环境配置 装好 PG/Redis/venv/nginx

set -euo pipefail

REMOTE="${REMOTE:-root@101.37.146.194}"
REMOTE_DIR="${REMOTE_DIR:-/opt/beeos}"
STAGE="${STAGE:-/tmp/beeos-deploy}"
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

# --- 1. 记录当前 commit，方便回滚 ---
HEAD_SHA=$(git rev-parse HEAD)
HEAD_SHORT=$(git rev-parse --short HEAD)
echo "==> deploying beeOS commit: $HEAD_SHORT ($HEAD_SHA)"

# --- 2. 打包源码 tarball ---
echo "==> staging source tarball at $STAGE/beeos-$HEAD_SHORT.tgz"
rm -rf "$STAGE"
mkdir -p "$STAGE"

# 源码部分（不含 venv / .env / 缓存）
tar -czf "$STAGE/beeos-$HEAD_SHORT.tgz" \
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
  apps packages deploy docs scripts pyproject.toml uv.lock

test -s "$STAGE/beeos-$HEAD_SHORT.tgz" || { echo "tarball empty"; exit 1; }

# --- 3. ship 到 ECS ---
echo "==> shipping tarball to $REMOTE"
run scp "$STAGE/beeos-$HEAD_SHORT.tgz" "$REMOTE:/tmp/"

# --- 4. 在 ECS 上：解压 + 同步 venv + 重启 ---
echo "==> deploying on $REMOTE"
run ssh "$REMOTE" "
  set -e
  cd $REMOTE_DIR

  # 4.1 解压 overlay
  echo '--- extracting source overlay ---'
  tar -xzf /tmp/beeos-$HEAD_SHORT.tgz

  # 4.2 修复 owner
  chown -R deploy:deploy $REMOTE_DIR/apps $REMOTE_DIR/packages $REMOTE_DIR/deploy $REMOTE_DIR/scripts $REMOTE_DIR/docs 2>/dev/null || true

  # 4.3 同步 Python venv（增量）
  $(if $SKIP_PIP; then echo "  echo '--- skipping venv sync (--skip-pip) ---'"; else echo "  sudo -u deploy bash -c '
    export PATH=\$HOME/.local/bin:\$PATH
    uv pip install --python ./venv/bin/python \\
      -e packages/beeos-core \\
      -e apps/queen \\
      -e apps/bee \\
      -e apps/boxes/month-close \\
      --index-url https://mirrors.aliyun.com/pypi/simple/ 2>&1 | tail -5
  '"; fi)

  # 4.4 reload systemd（如果 service 文件变了）+ restart Queen
  echo '--- restarting beeos-queen ---'
  systemctl daemon-reload
  systemctl restart beeos-queen
  sleep 3

  # 4.5 清理
  rm -f /tmp/beeos-$HEAD_SHORT.tgz
"

# --- 5. smoke checks ---
echo "==> smoke checks"
run ssh "$REMOTE" "
  set -e
  echo '--- 4 unit status ---'
  systemctl is-active nginx postgresql redis beeos-queen

  echo '--- Queen /health (127.0.0.1:8080) ---'
  curl -fsS http://127.0.0.1:8080/health

  echo '--- Portal /health (公网 nginx 80) ---'
  curl -fsS -o /dev/null -w '  http_code=%{http_code}\n' http://127.0.0.1:80/health

  echo '--- PostgreSQL SELECT 1 ---'
  PGPASSWORD=beeos-demo-password-change-me psql -h 127.0.0.1 -U beeos -d beeos -c 'SELECT 1;'

  echo '--- Redis ping ---'
  redis-cli ping
"

echo ""
echo "==> deploy complete"
echo "    deployed commit: $HEAD_SHORT"
echo "    rollback:        在本地 git checkout <previous-sha> 后重跑此脚本"
echo "    verify:          http://101.37.146.194/"
