#!/usr/bin/env bash
# 检查服务器是否满足 beeOS 部署要求
# 对应 [技术架构 §3.2 单机最小规格]

set -euo pipefail

echo "🐝 beeOS 服务器部署检查"
echo "================================"

# 1. OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "✅ OS: ${PRETTY_NAME:-unknown}"
    if [[ "${ID:-}" != "ubuntu" && "${ID:-}" != "debian" && "${ID:-}" != "centos" && "${ID:-}" != "rhel" ]]; then
        echo "⚠️  警告: 未在支持列表中测过 (Ubuntu/Debian/CentOS/RHEL)"
    fi
else
    echo "❌ 无法识别 OS"
fi

# 2. CPU
CPU_CORES=$(nproc 2>/dev/null || echo "0")
echo "✅ CPU: ${CPU_CORES} 核"
if [ "${CPU_CORES}" -lt 4 ]; then
    echo "❌ CPU 至少需要 4 核，当前 ${CPU_CORES}"
fi

# 3. RAM
TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_RAM_GB=$((TOTAL_RAM_KB / 1024 / 1024))
echo "✅ RAM: ${TOTAL_RAM_GB} GB"
if [ "${TOTAL_RAM_GB}" -lt 8 ]; then
    echo "❌ RAM 至少需要 8 GB，当前 ${TOTAL_RAM_GB}"
fi

# 4. Disk
DISK_FREE_GB=$(df -BG / | awk 'NR==2 {print $4}' | tr -d 'G')
echo "✅ 磁盘可用: ${DISK_FREE_GB} GB"
if [ "${DISK_FREE_GB}" -lt 50 ]; then
    echo "❌ 磁盘至少需要 50 GB 可用，当前 ${DISK_FREE_GB}"
fi

# 5. PostgreSQL
if command -v psql >/dev/null 2>&1; then
    PSQL_VERSION=$(psql --version | awk '{print $3}')
    echo "✅ PostgreSQL 客户端: ${PSQL_VERSION}"
    if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
        echo "✅ PostgreSQL 服务: 127.0.0.1:5432 可连"
    else
        echo "❌ PostgreSQL 未在 5432 监听（systemctl status postgresql）"
    fi
else
    echo "❌ psql 未安装（yum install postgresql）"
fi

# 6. Redis
if command -v redis-cli >/dev/null 2>&1; then
    REDIS_VERSION=$(redis-cli --version | awk '{print $2}')
    echo "✅ Redis 客户端: ${REDIS_VERSION}"
    if redis-cli -h 127.0.0.1 ping >/dev/null 2>&1; then
        echo "✅ Redis 服务: 127.0.0.1:6379 PONG"
    else
        echo "❌ Redis 未响应（systemctl status redis）"
    fi
else
    echo "❌ redis-cli 未安装（yum install redis）"
fi

# 7. beeos-queen systemd unit
if systemctl list-unit-files beeos-queen.service >/dev/null 2>&1; then
    STATE=$(systemctl is-enabled beeos-queen.service 2>/dev/null || echo "disabled")
    echo "✅ beeos-queen.service: ${STATE}"
else
    echo "⚠️  beeos-queen.service 未安装（部署时会 cp 到 /etc/systemd/system/）"
fi

# 8. Network
echo "✅ DNS 解析测试:"
if getent hosts api.deepseek.com >/dev/null 2>&1; then
    echo "   ✅ DeepSeek API 可达"
else
    echo "   ❌ DeepSeek API 不可达（需要公网出口）"
fi

if getent hosts dashscope.aliyuncs.com >/dev/null 2>&1; then
    echo "   ✅ 通义 API 可达"
else
    echo "   ❌ 通义 API 不可达（需要公网出口）"
fi

echo ""
echo "================================"
echo "检查完成。详细阈值见 docs/architecture/tech-architecture.md §3.2"
