CREATE TABLE IF NOT EXISTS telegram_subscribers (
    id            BIGSERIAL PRIMARY KEY,
    chat_id       BIGINT UNIQUE NOT NULL,
    username      TEXT,
    first_name    TEXT,
    subscribed_at TIMESTAMPTZ DEFAULT NOW(),
    active        BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_subscribers_active ON telegram_subscribers(active);
