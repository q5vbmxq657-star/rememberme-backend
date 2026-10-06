CREATE TABLE chat_weekly_usage (
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    request_id UUID NOT NULL,
    week_start DATE NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, request_id)
);
CREATE INDEX chat_weekly_usage_account_week ON chat_weekly_usage(user_id, week_start);
INSERT INTO schema_migrations(version) VALUES ('045_chat_weekly_usage');
