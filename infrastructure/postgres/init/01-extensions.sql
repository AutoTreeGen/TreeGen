-- =============================================================================
-- Инициализация расширений PostgreSQL.
-- Выполняется при первом старте контейнера (docker-entrypoint-initdb.d).
-- =============================================================================

-- Векторный поиск (для embeddings имён, мест, документов).
CREATE EXTENSION IF NOT EXISTS vector;

-- Триграммы (для fuzzy search по именам, особенно полезно для транслитерации).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- UUID-генерация (для первичных ключей).
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Криптография (для envelope encryption DNA-сегментов на app-level —
-- в качестве fallback, если не используется Cloud KMS локально).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Для unaccent-нормализации (поиск по диакритикам).
CREATE EXTENSION IF NOT EXISTS unaccent;
