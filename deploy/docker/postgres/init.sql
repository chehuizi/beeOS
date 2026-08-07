-- PostgreSQL 初始化 - beeOS MVP
-- 对应 [技术架构 §4.2 Hive 存储拆分]

-- 启用 pgvector 扩展（Granary 知识库用）
CREATE EXTENSION IF NOT EXISTS vector;

-- 启用 UUID 生成（分散式，避免外部依赖）
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 启用 pgcrypto（密码哈希 / 加密）
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 业务表由 SQLAlchemy 启动时自动创建（M1 阶段）
-- 此处只保留扩展启用，schema migration 由代码管理
