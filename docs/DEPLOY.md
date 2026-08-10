# beeOS ECS 部署 Runbook

> 镜像 [domain-box 的部署模式](../domain-box/../domain-box/docs/DEPLOY.md) 但**抛弃 Docker，全程原生 systemd**。
>
> **当前阶段**：IP-only，无 HTTPS。域名 / 证书 / 反代鉴权 等在 V1+ 接入。

## 主机

- **ECS**：阿里云 ECS（**与 domain-box 同台**：`101.37.146.194`）
- **访问方式**：直接 IP，**demo 阶段暂不绑域名**
- **OS**：阿里云 Linux 3（alinux 3，RHEL 8 兼容）

## 架构概览

```
ECS (101.37.146.194) — 4 个 native systemd units
├─ nginx.service          (80 → 8080)
├─ postgresql.service      (5432, 仅 127.0.0.1)
├─ redis.service           (6379, 仅 127.0.0.1)
└─ beeos-queen.service     (8080, 仅 127.0.0.1)
                            ├─ venv: /opt/beeos/venv
                            └─ .env: /opt/beeos/.env
```

**零 Docker**：所有服务都是系统包 + systemd，**1 人天部署**实际只需要 5 分钟。

**外部访问**：

- `http://101.37.146.194/health` → Queen 健康检查
- `http://101.37.146.194/api/` → Queen API（待 V1 实现）
- 后台端口（5432/6379/8080）**仅 127.0.0.1 监听**，通过 nginx 80 端口对外

---


**与 domain-box 的差异**：

| 维度 | domain-box | beeOS |
|---|---|---|
| 进程 | 单 Next.js 裸跑 | 4 个 native systemd unit（nginx / postgresql / redis / beeos-queen） |
| 数据 | SQLite 文件 | PostgreSQL + pgvector |
| 缓存 | 无 | Redis |
| systemd | `domainbox-console` | `beeos-queen`（外加 nginx / postgresql / redis 三个系统 unit） |
| 端口 | 4002 | 80（nginx）+ 5432 / 6379 / 8080 仅 127.0.0.1 |
| 部署单元 | tarball 源码 | tarball 源码 + `uv pip install` venv |

---

## 一次性环境配置

### 1. ECS 用户与目录

```bash
ssh root@101.37.146.194

# 创建 deploy 用户（如果还没有）
id deploy || useradd -m -s /bin/bash deploy

# beeOS 目录
mkdir -p /opt/beeos
chown -R deploy:deploy /opt/beeos
```

> **不用加 docker 组** —— 全部原生 systemd，不依赖 Docker

### 2. 安装 PostgreSQL + Redis（系统包）

```bash
# alinux 3 默认 PG 11（够 MVP 用，V1+ 升级 PG 14 + pgvector）
yum module enable -y postgresql:11
yum install -y postgresql-server postgresql-contrib redis

# 初始化 + 启动
postgresql-setup --initdb
systemctl enable --now postgresql
systemctl enable --now redis

# 创建 beeos 用户 + 数据库
sudo -u postgres psql -c "CREATE USER beeos WITH PASSWORD 'beeos-demo-password-change-me';"
sudo -u postgres psql -c "CREATE DATABASE beeos OWNER beeos;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE beeos TO beeos;"

# 改 pg_hba.conf 允许密码登录（默认是 ident）
sed -i "s|^host.*all.*all.*127.0.0.1/32.*ident|host all all 127.0.0.1/32 md5|" /var/lib/pgsql/data/pg_hba.conf
sed -i "s|^host.*all.*all.*::1/128.*ident|host all all ::1/128 md5|" /var/lib/pgsql/data/pg_hba.conf
systemctl reload postgresql

# 验证
PGPASSWORD=beeos-demo-password-change-me psql -h 127.0.0.1 -U beeos -d beeos -c "SELECT 1;"
redis-cli ping  # 期望 PONG
```

### 3. Systemd Unit（Queen）

```bash
# 复制（在 deploy/ 目录下）
cp /opt/beeos/deploy/systemd/beeos-queen.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now beeos-queen
```

`beeos-queen.service` 内容：

```ini
[Unit]
Description=beeOS Queen - 调度服务
After=network-online.target postgresql.service redis.service
Wants=network-online.target
Requires=postgresql.service redis.service

[Service]
Type=simple
User=deploy
Group=deploy
WorkingDirectory=/opt/beeos
EnvironmentFile=/opt/beeos/.env
Environment="PATH=/opt/beeos/venv/bin:/usr/local/bin:/usr/bin"
ExecStart=/opt/beeos/venv/bin/queen
Restart=on-failure
RestartSec=10
MemoryMax=600M
TasksMax=100

[Install]
WantedBy=multi-user.target
```

> **关键约束**：`Requires=postgresql.service redis.service` —— Queen 启动依赖 PG/Redis 先就绪。

### 4. 环境变量（host-resident，绝不进入 tarball）

`/opt/beeos/.env`：

```bash
# === 部署元信息 ===
BEEOOS_INSTANCE_ID=prod-01
BEEOOS_ENV=production
BEEOOS_LOG_LEVEL=INFO

# === 数据库（127.0.0.1 = 本机系统服务） ===
BEEOOS_POSTGRES_HOST=127.0.0.1
BEEOOS_POSTGRES_PORT=5432
BEEOOS_POSTGRES_DB=beeos
BEEOOS_POSTGRES_USER=beeos
BEEOOS_POSTGRES_PASSWORD=beeos-demo-password-change-me

# === Redis（同本机） ===
BEEOOS_REDIS_HOST=127.0.0.1
BEEOOS_REDIS_PORT=6379
BEEOOS_REDIS_PASSWORD=

# === Guardian (必须用强随机) ===
BEEOOS_MASTER_KEY=$(openssl rand -base64 32)
BEEOOS_API_TOKEN_SECRET=$(openssl rand -base64 32)
BEEOOS_API_TOKEN_TTL_HOURS=24

# === LLM API Keys ===
BEEOOS_LLM_PRIMARY=deepseek-chat
BEEOOS_LLM_PRIMARY_API_KEY=sk-...
BEEOOS_LLM_PRIMARY_BASE_URL=https://api.deepseek.com

BEEOOS_LLM_FALLBACK=qwen-plus
BEEOOS_LLM_FALLBACK_API_KEY=sk-...
BEEOOS_LLM_FALLBACK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# === 租户遥测 (生产必须 false) ===
BEEOOS_VENDOR_TELEMETRY_ENABLED=false

# === Portal ===
# demo 阶段：直接填 IP。V1 接入域名后改 https://<your-domain>
BEEOOS_PORTAL_URL=http://101.37.146.194
BEEOOS_CORS_ALLOWED_ORIGINS=["http://101.37.146.194"]
```

**生成强随机密钥**：

```bash
openssl rand -base64 32  # 用于 master_key 和 api_token_secret
```

### 5. 拉取源码 + 装 Python venv

```bash
cd /opt/beeos  # 已有源码目录（或从 git clone 拉）

# 装 deploy 用户的 uv
sudo -u deploy curl -LsSf https://astral.sh/uv/install.sh | sh

# 建 venv（用 deploy 用户，避免 root 路径权限问题）
sudo -u deploy bash -c "
  export PATH=\$HOME/.local/bin:\$PATH
  cd /opt/beeos
  uv venv --python 3.12 venv
  uv pip install \
    --python ./venv/bin/python \
    -e packages/beeos-core \
    -e apps/queen \
    -e apps/bee \
    -e apps/boxes/month-close \
    --index-url https://mirrors.aliyun.com/pypi/simple/
"
```

> `--index-url` 重要：ECS 包装 PyPI 出口走 aliyun 镜像，避免超时。

### 6. Nginx

```bash
cp /opt/beeos/deploy/nginx/beeos.conf /etc/nginx/conf.d/beeos.conf
nginx -t
systemctl reload nginx
```

### 7. 防火墙

```bash
# 阿里云安全组 + 本地 iptables 放行 80
sudo ufw allow 80/tcp
# 5432 / 6379 / 8080 必须仅 127.0.0.1 监听，绝不外暴露
```

---

## 部署验证

```bash
# 1. 检查 4 个 systemd 服务
systemctl list-units --type=service --state=running | grep -E "beeos|postgres|redis|nginx"

# 2. Queen /health
curl -fsS http://127.0.0.1:8080/health
# 期望: {"status":"ok","service":"queen","version":"0.1.0"}

# 3. 公网健康检查（通过 nginx）
curl -fsS http://101.37.146.194/health

# 4. PG 连接
PGPASSWORD=beeos-demo-password-change-me psql -h 127.0.0.1 -U beeos -d beeos -c "SELECT 1;"

# 5. Redis
redis-cli ping  # 期望 PONG
```

---

## 部署（无 Docker 流程）

```bash
# 本地
cd /Users/chehuizi/Desktop/code/beeOS

# 打包源码（不含 venv / node_modules / .env）
tar --exclude='.git' --exclude='venv' --exclude='node_modules' \
    --exclude='__pycache__' --exclude='*.pyc' \
    -czf /tmp/beeos-src.tgz \
    apps packages deploy docs scripts \
    pyproject.toml uv.lock README.md CLAUDE.md LICENSE

# Ship 到 ECS
scp /tmp/beeos-src.tgz root@101.37.146.194:/tmp/

# ECS 上解压
ssh root@101.37.146.194 '
  cd /opt/beeos
  tar -xzf /tmp/beeos-src.tgz
  chown -R deploy:deploy /opt/beeos
  # 同步 venv
  sudo -u deploy bash -c "
    export PATH=\$HOME/.local/bin:\$PATH
    cd /opt/beeos
    uv pip install --python ./venv/bin/python -e packages/beeos-core -e apps/queen -e apps/bee -e apps/boxes/month-close --index-url https://mirrors.aliyun.com/pypi/simple/ 2>&1 | tail -3
  "
  # 重启
  systemctl restart beeos-queen
'
```

---

## 部署后 Smoke Checks

```bash
# 系统服务状态
ssh root@101.37.146.194 'systemctl list-units --type=service --state=running | grep -E "beeos|postgres|redis|nginx"'

# Queen 健康
curl -fsS http://127.0.0.1:8080/health

# 公网（通过 nginx）
curl -fsS http://101.37.146.194/health

# 数据库连接
ssh root@101.37.146.194 'PGPASSWORD=beeos-demo-password-change-me psql -h 127.0.0.1 -U beeos -d beeos -c "SELECT 1;"'

# Redis
ssh root@101.37.146.194 'redis-cli ping'
```

---

## 回滚

```bash
# 1. 找到上一个 commit
git log --oneline -10

# 2. 切到上一个 commit，打包
git checkout <previous-sha>
tar -czf /tmp/beeos-prev.tgz ...

# 3. ECS 上覆盖 + 重启
ssh root@101.37.146.194 'cd /opt/beeos && tar -xzf /tmp/beeos-prev.tgz && systemctl restart beeos-queen'

# 4. 数据库回滚（如果 schema 不兼容）
ssh root@101.37.146.194 'pg_dump -U beeos beeos > /tmp/beeos-prev.sql'  # 部署前
ssh root@101.37.146.194 'psql -U beeos beeos < /tmp/beeos-prev.sql'   # 回滚后
```

---

## 日常运维

### 查看日志

```bash
# Queen journald 日志
ssh root@101.37.146.194 'journalctl -u beeos-queen -f'

# 最近 200 行
ssh root@101.37.146.194 'journalctl -u beeos-queen -n 200 --no-pager'

# nginx 访问/错误日志
ssh root@101.37.146.194 'tail -f /var/log/nginx/beeos.{access,error}.log'
```

### 备份

```bash
# DB 每日备份（写到 deploy/scripts/backup.sh，未实现 M1）
ssh root@101.37.146.194 'pg_dump -U beeos -h 127.0.0.1 beeos > /var/backups/beeos-$(date +%Y%m%d).sql'
```

### 升级

```bash
# 重跑部署脚本即可（scp 源码 + uv pip sync + restart）
bash scripts/deploy-to-ecs.sh
```

### 重启 Queen

```bash
ssh root@101.37.146.194 'systemctl restart beeos-queen'
```

### 切换主备模型

```bash
# 编辑 /opt/beeos/.env，交换 BEEOOS_LLM_PRIMARY / FALLBACK
ssh root@101.37.146.194 'systemctl restart beeos-queen'
```

### 凭证轮换

```bash
# 1. 在 Beekeeper Console /api/credentials 更新
# 2. 或手动：重启 Queen 加载新 .env
ssh root@101.37.146.194 'systemctl restart beeos-queen'
```

---

## 故障排查

| 现象 | 检查 |
|---|---|
| Portal 502 | `systemctl status beeos-queen` 看 Queen 是否健康 |
| Queen 启动失败 | `journalctl -u beeos-queen -n 200 --no-pager` |
| DB 连接失败 | `pg_isready -h 127.0.0.1 -p 5432` / `systemctl status postgresql` |
| Redis 连接失败 | `redis-cli -h 127.0.0.1 ping` / `systemctl status redis` |
| 模型调用慢 | `journalctl -u beeos-queen` 看 LLM 调用耗时 |
| 磁盘满 | `df -h /var/lib/beeos` |
| 内存爆 | `systemctl status beeos-queen` 看 MemoryMax / `free -h` |
| nginx 配置错 | `nginx -t` 然后 `systemctl reload nginx` |

---

## 待补（M1 之后）

- [ ] 自动每日备份脚本 `deploy/scripts/backup.sh`
- [ ] 证书自动续期（certbot timer）
- [ ] 监控告警（钉钉 webhook）
- [ ] 多 Bee 容器调度（V1+）
- [ ] k3s 集群升级路径（V1+）
