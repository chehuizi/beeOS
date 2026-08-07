# beeOS ECS 部署 Runbook

> 镜像 [domain-box 的部署模式](../domain-box/../domain-box/docs/DEPLOY.md) 但适配 Docker Compose 多服务架构。

## 主机

- **ECS**：阿里云 ECS（**与 domain-box 同台**：`101.37.146.194`）
- **域名**：复用 `www.agentbeeline.com` 的 nginx（**待定 — 是否给 beeOS 配独立子域名**）
- **OS**：Ubuntu 22.04 LTS

## 架构概览

```
ECS (101.37.146.194)
├─ nginx (80/443) → 已有，运行 agentbeeline.com
│   └─ 新增 /etc/nginx/conf.d/beeos.conf (待配置)
│
└─ beeOS 栈 (/opt/beeos)
   ├─ systemd unit: beeos.service
   ├─ Docker Compose
   │   ├─ postgres  (pgvector, 5432, 本地)
   │   ├─ redis     (6379, 本地)
   │   ├─ queen     (8080)
   │   └─ portal    (3000)
   └─ 数据卷
       ├─ postgres-data
       ├─ redis-data
       └─ box-data
```

**与 domain-box 的差异**：

| 维度 | domain-box | beeOS |
|---|---|---|
| 进程 | 单 Next.js 裸跑 | Docker Compose 4 容器 |
| 数据 | SQLite 文件 | PostgreSQL + pgvector |
| 缓存 | 无 | Redis |
| systemd | `domainbox-console` | `beeos` (管整个 compose) |
| 端口 | 4002 | 3000 + 8080（nginx 转发） |
| 部署单元 | tarball 源码 | tarball 源码 + docker images |

---

## 一次性环境配置

### 1. ECS 用户与目录

```bash
ssh root@101.37.146.194

# 创建 deploy 用户（如果还没有）
id deploy || useradd -m -s /bin/bash deploy
usermod -aG docker deploy

# beeOS 目录
mkdir -p /opt/beeos
chown -R deploy:deploy /opt/beeos

# 数据持久化目录
mkdir -p /var/lib/beeos/{postgres,redis,boxes}
chown -R deploy:deploy /var/lib/beeos
```

### 2. Docker 安装（如果还没有）

```bash
# Ubuntu 22.04 + Docker 官方源
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
systemctl enable docker
systemctl start docker
```

### 3. Systemd Unit

```bash
cat > /etc/systemd/system/beeos.service <<'EOF'
[Unit]
Description=beeOS - 私有化 AI 数字员工平台
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=deploy
WorkingDirectory=/opt/beeos
EnvironmentFile=/opt/beeos/.env
ExecStart=/usr/bin/docker compose -f /opt/beeos/docker-compose.yml up -d
ExecStop=/usr/bin/docker compose -f /opt/beeos/docker-compose.yml down
ExecReload=/usr/bin/docker compose -f /opt/beeos/docker-compose.yml restart
TimeoutStartSec=300
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable beeos
```

### 4. 环境变量（host-resident，绝不进入 tarball）

`/opt/beeos/.env`：

```bash
# === 部署元信息 ===
BEEOOS_INSTANCE_ID=prod-01
BEEOOS_ENV=production
BEEOOS_LOG_LEVEL=INFO

# === 数据库 ===
BEEOOS_POSTGRES_HOST=postgres
BEEOOS_POSTGRES_PORT=5432
BEEOOS_POSTGRES_DB=beeos
BEEOOS_POSTGRES_USER=beeos
BEEOOS_POSTGRES_PASSWORD=<from secrets manager>

# === Redis ===
BEEOOS_REDIS_HOST=redis
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
BEEOOS_PORTAL_URL=https://<your-domain>
BEEOOS_CORS_ALLOWED_ORIGINS=["https://<your-domain>"]
```

**生成强随机密钥**：

```bash
openssl rand -base64 32  # 用于 master_key 和 api_token_secret
```

### 5. Nginx

```bash
# 复制配置
sudo cp /opt/beeos/deploy/nginx/beeos.conf /etc/nginx/conf.d/beeos.conf

# 替换域名占位符
sudo sed -i 's/_/<your-domain>/g' /etc/nginx/conf.d/beeos.conf
sudo sed -i 's|/etc/nginx/ssl/beeos|/etc/nginx/ssl/<your-domain>|g' /etc/nginx/conf.d/beeos.conf

# 准备 SSL 证书（Let's Encrypt）
sudo certbot --nginx -d <your-domain>

# 测试 & 重启
sudo nginx -t
sudo systemctl reload nginx
```

### 6. 防火墙

```bash
# 阿里云安全组 + 本地 iptables 都放行
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
# 3000 / 5432 / 6379 / 8080 必须仅 127.0.0.1 监听，绝不外暴露
```

### 7. 主机环境检查

```bash
# 部署前必跑
bash /opt/beeos/deploy/scripts/check-server.sh
```

---

## 部署

```bash
# 本地
cd /Users/chehuizi/Desktop/code/beeOS
bash scripts/deploy-to-ecs.sh
```

脚本步骤：

1. 记录当前 commit
2. 本地 build docker 镜像
3. 打包源码 + 镜像 tarball
4. SCP 到 ECS `/tmp/`
5. SSH 到 ECS 加载镜像 + 覆盖源码
6. `systemctl restart beeos`
7. 跑 smoke checks

参数：

```bash
bash scripts/deploy-to-ecs.sh --dry-run        # 只打印
bash scripts/deploy-to-ecs.sh --skip-build     # 不重建镜像
bash scripts/deploy-to-ecs.sh --host user@ip   # 自定义目标
```

---

## 部署后 Smoke Checks

```bash
# Queen API 健康
curl -fsS http://127.0.0.1:8080/health

# Portal 渲染
curl -fsS -o /dev/null -w 'http_code=%{http_code}\n' http://127.0.0.1:3000/

# 容器状态
ssh root@101.37.146.194 'cd /opt/beeos && docker compose ps'

# 公网 HTTPS
curl -fsS https://<your-domain>/health

# 女王 API 鉴权（应当 401）
curl -fsS -o /dev/null -w 'http_code=%{http_code}\n' https://<your-domain>/api/v0/queen/jobs
```

---

## 回滚

脚本不自动 commit。回滚 = 部署上一个 commit：

```bash
# 1. 找到上一个 commit
git log --oneline -10

# 2. 切到上一个 commit，部署
git checkout <previous-sha>
bash scripts/deploy-to-ecs.sh

# 3. 切回主线
git checkout feature/init
```

### 数据库回滚（更复杂）

数据迁移一旦上线，**回滚需要手动**：

```bash
# 1. 部署前手动备份
ssh root@101.37.146.194 'docker compose exec -T postgres pg_dump -U beeos beeos > /tmp/beeos-prev.sql'

# 2. 回滚到旧版本
git checkout <previous-sha>
bash scripts/deploy-to-ecs.sh

# 3. 如果 schema 不兼容，需要恢复 DB
ssh root@101.37.146.194 'cat /tmp/beeos-prev.sql | docker compose exec -T postgres psql -U beeos beeos'
```

**M1 阶段 schema 频繁变动**，建议每次部署前都 dump 一份。

---

## 日常运维

### 查看日志

```bash
# 用 journald（systemd 启动）
ssh root@101.37.146.194 'journalctl -u beeos -f'

# 直接看容器日志
ssh root@101.37.146.194 'cd /opt/beeos && docker compose logs -f'

# 单容器
ssh root@101.37.146.194 'docker compose logs -f queen'
```

### 备份

```bash
# DB 每日备份（写到 deploy/scripts/backup.sh，未实现 M1）
ssh root@101.37.146.194 'docker compose exec -T postgres pg_dump -U beeos beeos > /var/backups/beeos-$(date +%Y%m%d).sql'
```

### 升级镜像

```bash
# 拉新镜像 + 重启
ssh root@101.37.146.194 'cd /opt/beeos && docker compose pull && systemctl restart beeos'
```

### 重启单个服务

```bash
ssh root@101.37.196.194 'cd /opt/beeos && docker compose restart queen'
```

### 切换主备模型

```bash
# 编辑 /opt/beeos/.env，交换 BEEOOS_LLM_PRIMARY / FALLBACK
ssh root@101.37.146.194 'cd /opt/beeos && docker compose restart queen'
```

### 凭证轮换

```bash
# 1. 在 Beekeeper Console /api/credentials 更新
# 2. 或手动：重启 Queen 加载新 .env
ssh root@101.37.146.194 'systemctl restart beeos'
```

---

## 故障排查

| 现象 | 检查 |
|---|---|
| Portal 502 | `docker compose ps` 看 queen 是否健康 |
| Queen 启动失败 | `docker compose logs queen` |
| DB 连接失败 | `docker compose exec postgres pg_isready` |
| 模型调用慢 | `journalctl -u beeos` 看 LLM 调用耗时 |
| 磁盘满 | `df -h /var/lib/beeos` |
| 内存爆 | `docker stats` |

---

## 待补（M1 之后）

- [ ] 自动每日备份脚本 `deploy/scripts/backup.sh`
- [ ] 证书自动续期（certbot timer）
- [ ] 监控告警（钉钉 webhook）
- [ ] 多 Bee 容器调度（V1+）
- [ ] k3s 集群升级路径（V1+）
