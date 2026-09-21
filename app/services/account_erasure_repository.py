from contextlib import contextmanager
from uuid import UUID

import psycopg


class AccountErasureRepository:
    def __init__(self, *, database_url: str):
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError('Account deletion requires a persistent repository.')
        self.database_url = database_url

    def begin(self, user_id: UUID) -> None:
        with psycopg.connect(self.database_url) as connection:
            exists = connection.execute('SELECT user_id FROM users WHERE user_id = %s FOR UPDATE', (user_id,)).fetchone()
            if not exists:
                if connection.execute('SELECT 1 FROM account_erasure_requests WHERE user_id = %s', (user_id,)).fetchone():
                    return
                raise ValueError('Account not found.')
            connection.execute('INSERT INTO account_erasure_requests(user_id) VALUES (%s) ON CONFLICT DO NOTHING', (user_id,))
            connection.execute("UPDATE users SET status = 'disabled' WHERE user_id = %s", (user_id,))

    @contextmanager
    def claim(self, user_id: UUID):
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            locked = connection.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 23))', (str(user_id),)).fetchone()[0]
            yield locked

    def stage(self, user_id: UUID) -> str:
        with psycopg.connect(self.database_url) as connection:
            return connection.execute('SELECT stage FROM account_erasure_requests WHERE user_id = %s', (user_id,)).fetchone()[0]

    def advance(self, user_id: UUID, expected: str, stage: str) -> None:
        with psycopg.connect(self.database_url) as connection:
            result = connection.execute("""UPDATE account_erasure_requests SET stage = %s,
                updated_at = NOW(), retry_at = NOW(),
                completed_at = CASE WHEN %s = 'completed' THEN NOW() ELSE NULL END
                WHERE user_id = %s AND stage = %s""", (stage, stage, user_id, expected))
            if result.rowcount != 1:
                raise RuntimeError('Account deletion changed during recovery.')

    def retry(self, user_id: UUID) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute("""UPDATE account_erasure_requests SET attempts = attempts + 1,
                updated_at = NOW(), retry_at = NOW() + INTERVAL '5 minutes'
                WHERE user_id = %s AND stage <> 'completed'""", (user_id,))

    def pending(self, *, limit: int = 100) -> list[UUID]:
        if not 1 <= limit <= 1000:
            raise ValueError('Invalid recovery batch size.')
        with psycopg.connect(self.database_url) as connection:
            return [row[0] for row in connection.execute("""SELECT user_id FROM account_erasure_requests
                WHERE stage <> 'completed' AND retry_at <= NOW() ORDER BY requested_at LIMIT %s""", (limit,))]
