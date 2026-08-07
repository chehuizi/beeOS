#!/usr/bin/env bash
# beeOS ECS 部署脚本
# 镜像 domain-box/scripts/deploy-to-ecs.sh 模式，但适配 Docker Compose 多服务
#
# 与 domain-box 的差异：
#   - domain-box: 单 Next.js 进程 + 裸跑
#   - beeOS: 多服务（PG + Redis + Queen + Portal）→ Docker Compose, systemd 管编排
#   - 镜像本地 build → tarball 到 ECS → docker load → docker compose up
#
# 用法：
#   bash scripts/deploy-to-ecs.sh                  # 完整部署
#   bash scripts/deploy-to-ecs.sh --dry-run        # 只打印命令
#   bash scripts/deploy-to-ecs.sh --skip-build     # 不重建镜像
#   bash scripts/deploy-to-ecs.sh --host <user@ip> # 自定义目标
#
# 部署前清单：
#   1. 提交所有要发的改动（脚本不自动 commit）
#   2. 本地有 docker（用于 build 镜像）
#   3. 本地 ssh 能免密登录 ECS（~/.ssh/config 或 key in known_hosts）
#   4. ECS 上 /opt/beeos/.env 已就位（脚本不传 .env，全部 host-resident）

set -euo pipefail

REMOTE="${REMOTE:-root@101.37.146.194}"
REMOTE_DIR="${REMOTE_DIR:-/opt/beeos}"
STAGE="${STAGE:-/tmp/beeos-deploy}"
LOCAL="${LOCAL:-$PWD}"

DRY_RUN=false
SKIP_BUILD=false
for arg in "$*"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --skip-build) SKIP_BUILD=true ;;
    --host) shift; REMOTE="$1"; shift ;;
    -h|--help)
      echo "Usage: $0 [--dry-run] [--skip-build] [--host user@host]"
      exit 0
      ;;
    *) echo "unknown arg: $arg" >&2; exit 1 ;;
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

# --- 2. 本地 build 镜像 ---
if ! $SKIP_BUILD; then
  echo "==> building docker images locally"
  run docker compose build
fi

# --- 3. 打包源代码 + docker-compose.yml ---
echo "==> staging tarball at $STAGE/beeos-$HEAD_SHORT.tgz"
rm -rf "$STAGE"
mkdir -p "$STAGE"

# 源码部分（不含 node_modules / .venv / .next / 大文件）
tar -czf "$STAGE/beeos-$HEAD_SHORT.tgz" \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='.env.local' \
  --exclude='venv' \
  --exclude='.venv' \
  --exclude='node_modules' \
  --exclude='apps/portal/node_modules' \
  --exclude='apps/portal/.next' \
  --exclude='.pytest_cache' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.egg-info' \
  --exclude='coverage' \
  --exclude='htmlcov' \
  apps packages deploy docs scripts docker-compose.yml pyproject.toml uv.lock 2>/dev/null || \
tar -czf "$STAGE/beeos-$HEAD_SHORT.tgz" \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='.env.local' \
  --exclude='venv' \
  --exclude='.venv' \
  --exclude='node_modules' \
  --exclude='apps/portal/node_modules' \
  --exclude='apps/portal/.next' \
  --exclude='.pytest_cache' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.egg-info' \
  --exclude='coverage' \
  --exclude='htmlcov' \
  apps packages deploy docs scripts docker-compose.yml pyproject.toml

test -s "$STAGE/beeos-$HEAD_SHORT.tgz" || { echo "tarball empty"; exit 1; }

# --- 4. 镜像 tarball（应用定义所需镜像）---
echo "==> exporting docker images"
mkdir -p "$STAGE/images"
for service in postgres redis queen portal; do
  image=$(docker compose config --images 2>/dev/null | grep -E "($service|beeos).*(:|$)" | head -1 || echo "")
  if [ -n "$image" ]; then
    echo "    saving $image"
    run docker save "$image" -o "$STAGE/images/${service}.tar"
  fi
done

# 把 images 子目录压进主 tarball
tar -czf "$STAGE/beeos-images-$HEAD_SHORT.tgz" -C "$STAGE" images

# --- 5. ship 到 ECS ---
echo "==> shipping tarballs to $REMOTE"
run scp "$STAGE/beeos-$HEAD_SHORT.tgz" "$REMOTE:/tmp/"
run scp "$STAGE/beeos-images-$HEAD_SHORT.tgz" "$REMOTE:/tmp/"

# --- 6. 在 ECS 上：解压 + 加载镜像 + 重启 ---
echo "==> deploying on $REMOTE"
run ssh "$REMOTE" "
  set -e

  # 6.1 准备目录
  mkdir -p $REMOTE_DIR
  cd /tmp

  # 6.2 加载 docker 镜像
  echo '--- loading docker images ---'
  rm -rf /tmp/beeos-images
  mkdir /tmp/beeos-images
  tar -xzf /tmp/beeos-images-$HEAD_SHORT.tgz -C /tmp/beeos-images
  for img in /tmp/beeos-images/images/*.tar; do
    [ -f \"\$img\" ] || continue
    docker load -i \"\$img\" < /dev/null
  done
  rm -rf /tmp/beeos-images
  rm -f /tmp/beeos-images-$HEAD_SHORT.tgz

  # 6.3 解压源码到 overlay 目录
  echo '--- extracting source overlay ---'
  rm -rf /tmp/beeos-extract
  mkdir /tmp/beeos-extract
  tar -xzf /tmp/beeos-$HEAD_SHORT.tgz -C /tmp/beeos-extract

  # 6.4 清理旧 src（避免 stale 文件干扰）
  rm -rf $REMOTE_DIR/apps/queen/src $REMOTE_DIR/apps/bee/src
  rm -rf $REMOTE_DIR/apps/boxes/month-close/src $REMOTE_DIR/apps/portal/src
  rm -rf $REMOTE_DIR/packages/beeos-core/src
  rm -rf $REMOTE_DIR/deploy $REMOTE_DIR/scripts $REMOTE_DIR/docs

  # 6.5 拷贝新文件
  cp -r --no-preserve=mode,ownership /tmp/beeos-extract/apps $REMOTE_DIR/
  cp -r --no-preserve=mode,ownership /tmp/beeos-extract/packages $REMOTE_DIR/
  cp -r --no-preserve=mode,ownership /tmp/beeos-extract/deploy $REMOTE_DIR/
  cp -r --no-preserve=mode,ownership /tmp/beeos-extract/scripts $REMOTE_DIR/ || true
  cp -r --no-preserve=mode,ownership /tmp/beeos-extract/docs $REMOTE_DIR/ || true
  cp --no-preserve=mode,ownership /tmp/beeos-extract/docker-compose.yml $REMOTE_DIR/
  cp --no-preserve=mode,ownership /tmp/beeos-extract/pyproject.toml $REMOTE_DIR/ || true
  cp --no-preserve=mode,ownership /tmp/beeos-extract/uv.lock $REMOTE_DIR/ || true

  chown -R deploy:deploy $REMOTE_DIR/apps $REMOTE_DIR/packages $REMOTE_DIR/deploy $REMOTE_DIR/scripts $REMOTE_DIR/docs $REMOTE_DIR/docker-compose.yml 2>/dev/null || true

  rm -rf /tmp/beeos-extract
  rm -f /tmp/beeos-$HEAD_SHORT.tgz
"

# --- 7. 重启服务 ---
echo "==> restarting beeos"
run ssh "$REMOTE" "systemctl restart beeos && sleep 5"

# --- 8. smoke checks ---
echo "==> smoke checks"
run ssh "$REMOTE" "
  set -e
  echo '--- queen /health ---'
  curl -fsS http://127.0.0.1:8080/health || echo '  queen /health failed'
  echo '--- portal / ---'
  curl -fsS -o /dev/null -w '  http_code=%{http_code}\n' http://127.0.0.1:3000/ || echo '  portal / failed'
  echo '--- docker compose ps ---'
  cd $REMOTE_DIR && docker compose ps
"

echo ""
echo "==> deploy complete"
echo "    deployed commit: $HEAD_SHORT"
echo "    rollback:        ssh $REMOTE  然后 cd /opt/beeos && git checkout <previous-sha> （先在 ECS 上 git init）"
echo "    verify:          https://<your-domain>/"
