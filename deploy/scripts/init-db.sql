-- beeOS 本机开发 PG 初始化脚本
-- 用途：在本机 PostgreSQL 创建 beeos 用户和数据库
-- 调⽤：make init-db（内部调 sudo -u postgres psql -f deploy/scripts/init-db.sql）
-- 重跑前请先 make db-reset（drop + recreate）
-- 部署到 ECS 时同样适用（postgresql.service 启动后跑一遍）

-- === 幂等创建用户 ===
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'beeos') THEN
    CREATE ROLE beeos WITH LOGIN PASSWORD 'beeos-dev-password';
  ELSE
    ALTER ROLE beeos WITH LOGIN PASSWORD 'beeos-dev-password';
  END IF;
END
$$;

-- === 幂等创建数据库（PG 不支持 IF NOT EXISTS for CREATE DATABASE）===
SELECT 'CREATE DATABASE beeos OWNER beeos'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'beeos')\gexec

-- === 授权 ===
GRANT ALL PRIVILEGES ON DATABASE beeos TO beeos;

-- === beeos 用户能建库（让 Base.metadata.create_all 跑得动）===
\c beeos
GRANT ALL ON SCHEMA public TO beeos;
ALTER SCHEMA public OWNER TO beeos;
