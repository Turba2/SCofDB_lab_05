-- ============================================
-- LAB 05: Событийная инвалидация кэша
-- ============================================

CREATE TABLE IF NOT EXISTS cache_invalidation_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type VARCHAR(64) NOT NULL,
    entity_type VARCHAR(64) NOT NULL,
    entity_id UUID NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cache_events_unprocessed
    ON cache_invalidation_events (processed, created_at);

-- В текущей реализации приложения используется минимальный вариант:
-- синхронный invalidate в коде после обновления заказа.
-- Таблица оставлена как задел под DB outbox / background worker.
