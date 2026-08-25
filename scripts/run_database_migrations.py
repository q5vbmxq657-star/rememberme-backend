from __future__ import annotations


from dataclasses import dataclass
from enum import Enum
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple, Sequence

import psycopg
from psycopg.rows import dict_row


MIGRATION_PATTERN = re.compile(
    r"^(?P<version>\d{3}_[a-z0-9_]+)\.sql$"
)

DOLLAR_TAG_PATTERN = re.compile(
    r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$"
)

OUTER_BEGIN_PATTERN = re.compile(
    r"\ABEGIN\s*;",
    re.IGNORECASE,
)

OUTER_COMMIT_PATTERN = re.compile(
    r"COMMIT\s*;\s*\Z",
    re.IGNORECASE,
)

MIGRATION_RECORD_PATTERN = re.compile(
    r"\bINSERT\s+INTO\s+"
    r"(?:public\.)?"
    r"schema_migrations\b",
    re.IGNORECASE,
)

MIGRATION_LOCK_KEYS = (
    1_397_903_193,
    1_296_651_474,
)

DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_STATEMENT_TIMEOUT_MS = 120_000
DEFAULT_IDLE_TRANSACTION_TIMEOUT_MS = 30_000

NORMALIZATION_NONE = "none"
NORMALIZATION_LEGACY_OUTER_TRANSACTION_REMOVED = (
    "legacy_outer_transaction_removed"
)

LEGACY_MIGRATION_SOURCE_HASHES = {
    "002_digital_human_profiles":
        "749f480c6d64d88d5cfd0f8085f938af4cb8c4b11f22640f1b3855fe0fc6d649",
    "003_avatar_evidence_assets":
        "7437131506942236e03c906bfd91594abacf7074930f0e70a05b91360b7f636f",
    "004_identity_verification_receipts":
        "d1dce1e68ca9671a92053346108929e8f57ade3d8b74c15223b135a4fe828f5d",
    "005_generated_preview_jobs":
        "51f01328ca58aeb12cf8a5915ea768e27d6a56435c79e7155d060fc53126aa64",
    "006_profile_erasure_requests":
        "43a8d6d88094ed0e1eb6b3eaa2af16dfebd6511ed84279fdc99cf7a9b135ad4a",
    "007_profile_erasure_resume_authority":
        "53d818a70fc21ed4e3b97379c8e08edabfc86c8ccb88bdaf6f0bef03b63f306f",
}

LEGACY_MIGRATION_EXECUTION_HASHES = {
    "002_digital_human_profiles":
        "f279773d6109682c89bc154ea93ddf32716add6564a40ca01b3096e46447f18a",
    "003_avatar_evidence_assets":
        "775dde0292d44215fa8d5ed2e9f35e1eedf479316c586976a59d0143c0144929",
    "004_identity_verification_receipts":
        "c952126c241a99c65d361e0a667ea425866f9fcc05b80bdbdbb58fe5b8e0c482",
    "005_generated_preview_jobs":
        "0f174c4fd95cfc5e14dc61bdc7a0456fa360af7098347ef16f01890011e02584",
    "006_profile_erasure_requests":
        "c67bb59f31cfe6627face45c7439360db2196d63aacec4f861748545bef93149",
    "007_profile_erasure_resume_authority":
        "dcf06469fe9f9f64ea6cffc8e8fa52675acebe7637057a6e903041fc102cb86d",
}


class MigrationRunnerError(RuntimeError):
    pass


class SQLToken(NamedTuple):
    kind: str
    start: int
    end: int
    text: str


class Migration(NamedTuple):
    version: str
    path: Path
    source_sha256: str
    execution_sha256: str
    execution_sql: str
    normalization_mode: str

    @property
    def numeric_version(self) -> int:
        return int(
            self.version.split(
                "_",
                1,
            )[0]
        )

    @property
    def sha256(self) -> str:
        return self.source_sha256

AUDIT_AUTHORITY_MIGRATION_VERSION = (
    "008_migration_audit_authority"
)


class MigrationAuthorityState(
    str,
    Enum,
):
    EMPTY_UNBOOTSTRAPPED = "empty_unbootstrapped"
    HISTORICAL_UNAUDITED = "historical_unaudited"
    INVALID_AUDIT_WITHOUT_VERSION_TABLE = (
        "invalid_audit_without_version_table"
    )
    INVALID_008_APPLIED_WITHOUT_AUDIT_TABLE = (
        "invalid_008_applied_without_audit_table"
    )
    INVALID_AUDIT_TABLE_WITHOUT_008_VERSION = (
        "invalid_audit_table_without_008_version"
    )
    BOOTSTRAPPED_VALID = "bootstrapped_valid"
    BOOTSTRAPPED_INCOMPLETE_AUDIT = (
        "bootstrapped_incomplete_audit"
    )
    BOOTSTRAPPED_HASH_MISMATCH = (
        "bootstrapped_hash_mismatch"
    )
    UNKNOWN_APPLIED_VERSION = (
        "unknown_applied_version"
    )
    APPLIED_VERSION_GAP = "applied_version_gap"


@dataclass(frozen=True)
class MigrationAuthorityInspection:
    state: MigrationAuthorityState
    applied: dict[str, object]
    audit_rows: dict[str, dict[str, object]]
    version_table_present: bool
    audit_table_present: bool


@dataclass(frozen=True)
class MigrationExecutionRecord:
    version: str
    source_sha256: str
    execution_sha256: str
    normalization_mode: str
    duration_ms: int
    applied_at: object



def text_sha256(
    value: str,
) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def file_sha256(
    path: Path,
) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def lex_sql(
    source: str,
) -> list[SQLToken]:
    tokens: list[SQLToken] = []
    index = 0
    length = len(source)

    while index < length:
        character = source[index]

        if source.startswith("--", index):
            end = source.find("\n", index)

            if end == -1:
                end = length

            tokens.append(
                SQLToken(
                    "line_comment",
                    index,
                    end,
                    source[index:end],
                )
            )

            index = end
            continue

        if source.startswith("/*", index):
            depth = 1
            cursor = index + 2

            while cursor < length and depth > 0:
                if source.startswith("/*", cursor):
                    depth += 1
                    cursor += 2
                    continue

                if source.startswith("*/", cursor):
                    depth -= 1
                    cursor += 2
                    continue

                cursor += 1

            if depth != 0:
                raise MigrationRunnerError(
                    "Unterminated SQL block comment."
                )

            tokens.append(
                SQLToken(
                    "block_comment",
                    index,
                    cursor,
                    source[index:cursor],
                )
            )

            index = cursor
            continue

        if character == "'":
            cursor = index + 1

            while cursor < length:
                if source[cursor] == "'":
                    if (
                        cursor + 1 < length
                        and source[cursor + 1] == "'"
                    ):
                        cursor += 2
                        continue

                    cursor += 1
                    break

                cursor += 1
            else:
                raise MigrationRunnerError(
                    "Unterminated SQL string literal."
                )

            tokens.append(
                SQLToken(
                    "single_quoted_string",
                    index,
                    cursor,
                    source[index:cursor],
                )
            )

            index = cursor
            continue

        if character == '"':
            cursor = index + 1

            while cursor < length:
                if source[cursor] == '"':
                    if (
                        cursor + 1 < length
                        and source[cursor + 1] == '"'
                    ):
                        cursor += 2
                        continue

                    cursor += 1
                    break

                cursor += 1
            else:
                raise MigrationRunnerError(
                    "Unterminated quoted identifier."
                )

            tokens.append(
                SQLToken(
                    "quoted_identifier",
                    index,
                    cursor,
                    source[index:cursor],
                )
            )

            index = cursor
            continue

        if character == "$":
            match = DOLLAR_TAG_PATTERN.match(
                source,
                index,
            )

            if match is not None:
                tag = match.group(0)

                end = source.find(
                    tag,
                    match.end(),
                )

                if end == -1:
                    raise MigrationRunnerError(
                        "Unterminated dollar-quoted body."
                    )

                end += len(tag)

                tokens.append(
                    SQLToken(
                        "dollar_quoted_body",
                        index,
                        end,
                        source[index:end],
                    )
                )

                index = end
                continue

        cursor = index + 1

        while cursor < length:
            if source.startswith("--", cursor):
                break

            if source.startswith("/*", cursor):
                break

            if source[cursor] in {"'", '"', "$"}:
                break

            cursor += 1

        tokens.append(
            SQLToken(
                "plain_sql",
                index,
                cursor,
                source[index:cursor],
            )
        )

        index = cursor

    return tokens


def plain_sql_projection(
    tokens: Sequence[SQLToken],
) -> str:
    return "".join(
        token.text
        if token.kind == "plain_sql"
        else " " * (token.end - token.start)
        for token in tokens
    )


def dollar_quoted_bodies(
    tokens: Sequence[SQLToken],
) -> list[str]:
    return [
        token.text
        for token in tokens
        if token.kind == "dollar_quoted_body"
    ]


def has_outer_transaction_wrapper(
    source: str,
) -> bool:
    projection = plain_sql_projection(
        lex_sql(source)
    )

    return (
        OUTER_BEGIN_PATTERN.search(
            projection
        )
        is not None
        or OUTER_COMMIT_PATTERN.search(
            projection
        )
        is not None
    )


def normalize_legacy_migration(
    *,
    version: str,
    source: str,
    source_sha256: str,
) -> tuple[str, str, str]:
    expected_source_hash = (
        LEGACY_MIGRATION_SOURCE_HASHES.get(
            version
        )
    )

    if expected_source_hash is None:
        if has_outer_transaction_wrapper(source):
            raise MigrationRunnerError(
                "Future migration contains an outer "
                "transaction wrapper: "
                f"{version}"
            )

        return (
            source,
            text_sha256(source),
            NORMALIZATION_NONE,
        )

    if source_sha256 != expected_source_hash:
        raise MigrationRunnerError(
            "Legacy migration source hash mismatch: "
            f"{version}; "
            f"expected={expected_source_hash}; "
            f"actual={source_sha256}"
        )

    original_tokens = lex_sql(source)

    original_bodies = dollar_quoted_bodies(
        original_tokens
    )

    projection = plain_sql_projection(
        original_tokens
    )

    begin_match = OUTER_BEGIN_PATTERN.search(
        projection
    )

    commit_match = OUTER_COMMIT_PATTERN.search(
        projection
    )

    if begin_match is None or commit_match is None:
        raise MigrationRunnerError(
            "Authorized legacy migration does not "
            "contain the expected outer wrapper: "
            f"{version}"
        )

    if begin_match.end() >= commit_match.start():
        raise MigrationRunnerError(
            "Invalid legacy transaction wrapper spans: "
            f"{version}"
        )

    execution_sql = (
        source[
            begin_match.end():
            commit_match.start()
        ]
        .lstrip("\r\n")
        .rstrip()
        + "\n"
    )

    execution_tokens = lex_sql(
        execution_sql
    )

    execution_projection = plain_sql_projection(
        execution_tokens
    )

    if (
        OUTER_BEGIN_PATTERN.search(
            execution_projection
        )
        is not None
    ):
        raise MigrationRunnerError(
            "Normalized migration still begins "
            "with an outer transaction: "
            f"{version}"
        )

    if (
        OUTER_COMMIT_PATTERN.search(
            execution_projection
        )
        is not None
    ):
        raise MigrationRunnerError(
            "Normalized migration still ends "
            "with an outer transaction: "
            f"{version}"
        )

    if (
        dollar_quoted_bodies(
            execution_tokens
        )
        != original_bodies
    ):
        raise MigrationRunnerError(
            "Dollar-quoted migration bodies changed "
            "during normalization: "
            f"{version}"
        )

    migration_record_count = len(
        MIGRATION_RECORD_PATTERN.findall(
            execution_projection
        )
    )

    if migration_record_count != 1:
        raise MigrationRunnerError(
            "Normalized migration must record its "
            "version exactly once: "
            f"{version}; "
            f"count={migration_record_count}"
        )

    execution_sha256 = text_sha256(
        execution_sql
    )

    expected_execution_hash = (
        LEGACY_MIGRATION_EXECUTION_HASHES[
            version
        ]
    )

    if execution_sha256 != expected_execution_hash:
        raise MigrationRunnerError(
            "Legacy migration execution hash "
            "mismatch: "
            f"{version}; "
            f"expected={expected_execution_hash}; "
            f"actual={execution_sha256}"
        )

    return (
        execution_sql,
        execution_sha256,
        NORMALIZATION_LEGACY_OUTER_TRANSACTION_REMOVED,
    )


def discover_migrations(
    migration_root: Path,
) -> list[Migration]:
    if not migration_root.is_dir():
        raise MigrationRunnerError(
            "Migration directory does not exist: "
            f"{migration_root}"
        )

    migrations: list[Migration] = []

    for path in sorted(
        migration_root.glob("*.sql")
    ):
        match = MIGRATION_PATTERN.fullmatch(
            path.name
        )

        if match is None:
            raise MigrationRunnerError(
                "Unsupported migration filename: "
                f"{path.name}"
            )

        version = match.group("version")

        source = path.read_text(
            encoding="utf-8"
        )

        if not source.strip():
            raise MigrationRunnerError(
                f"Migration is empty: {path.name}"
            )

        source_sha256 = text_sha256(source)

        (
            execution_sql,
            execution_sha256,
            normalization_mode,
        ) = normalize_legacy_migration(
            version=version,
            source=source,
            source_sha256=source_sha256,
        )

        migrations.append(
            Migration(
                version=version,
                path=path,
                source_sha256=source_sha256,
                execution_sha256=execution_sha256,
                execution_sql=execution_sql,
                normalization_mode=normalization_mode,
            )
        )

    if not migrations:
        raise MigrationRunnerError(
            "No versioned SQL migrations were found."
        )

    versions = [
        migration.version
        for migration in migrations
    ]

    if len(versions) != len(set(versions)):
        raise MigrationRunnerError(
            "Duplicate migration versions detected."
        )

    numeric_versions = [
        migration.numeric_version
        for migration in migrations
    ]

    expected_versions = list(
        range(
            numeric_versions[0],
            numeric_versions[-1] + 1,
        )
    )

    if numeric_versions != expected_versions:
        raise MigrationRunnerError(
            "Migration version gap detected: "
            f"found={numeric_versions}; "
            f"expected={expected_versions}"
        )

    return migrations


def validate_timeout_values(
    *,
    lock_timeout_seconds: float,
    statement_timeout_ms: int,
    idle_transaction_timeout_ms: int,
) -> None:
    if lock_timeout_seconds <= 0:
        raise MigrationRunnerError(
            "Migration lock timeout must be positive."
        )

    if statement_timeout_ms <= 0:
        raise MigrationRunnerError(
            "Statement timeout must be positive."
        )

    if idle_transaction_timeout_ms <= 0:
        raise MigrationRunnerError(
            "Idle transaction timeout must be positive."
        )


def configure_session(
    connection: psycopg.Connection,
    *,
    statement_timeout_ms: int,
    idle_transaction_timeout_ms: int,
) -> None:
    requested = {
        "statement_timeout":
            statement_timeout_ms,
        "idle_in_transaction_session_timeout":
            idle_transaction_timeout_ms,
    }

    with connection.cursor(
        row_factory=dict_row,
    ) as cursor:
        for setting_name, value_ms in requested.items():
            cursor.execute(
                """
                SELECT set_config(
                    %s::text,
                    %s::text,
                    FALSE
                )
                """,
                (
                    setting_name,
                    f"{value_ms}ms",
                ),
            )

        cursor.execute(
            """
            SELECT
                name,
                setting::bigint AS setting_ms,
                unit,
                source
            FROM pg_settings
            WHERE name = ANY(%s::text[])
            ORDER BY name
            """,
            (
                list(requested),
            ),
        )

        rows = {
            str(row["name"]): dict(row)
            for row in cursor.fetchall()
        }

    for setting_name, expected_ms in requested.items():
        row = rows.get(setting_name)

        if row is None:
            raise MigrationRunnerError(
                "PostgreSQL did not return timeout "
                f"setting: {setting_name}"
            )

        if row["unit"] != "ms":
            raise MigrationRunnerError(
                "Unexpected PostgreSQL timeout unit "
                f"for {setting_name}: {row['unit']}"
            )

        if int(row["setting_ms"]) != expected_ms:
            raise MigrationRunnerError(
                "PostgreSQL timeout mismatch for "
                f"{setting_name}: "
                f"expected={expected_ms}; "
                f"actual={row['setting_ms']}"
            )

        if row["source"] != "session":
            raise MigrationRunnerError(
                "PostgreSQL timeout is not "
                "session-scoped: "
                f"{setting_name}; "
                f"source={row['source']}"
            )


def acquire_migration_lock(
    connection: psycopg.Connection,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds

    while True:
        with connection.cursor(
            row_factory=dict_row,
        ) as cursor:
            cursor.execute(
                """
                SELECT pg_try_advisory_lock(
                    %s::integer,
                    %s::integer
                ) AS acquired
                """,
                MIGRATION_LOCK_KEYS,
            )

            row = cursor.fetchone()

        if row is not None and bool(row["acquired"]):
            return

        if time.monotonic() >= deadline:
            raise MigrationRunnerError(
                "Timed out waiting for the canonical "
                "database migration lock."
            )

        time.sleep(0.1)


def release_migration_lock(
    connection: psycopg.Connection,
) -> None:
    with connection.cursor(
        row_factory=dict_row,
    ) as cursor:
        cursor.execute(
            """
            SELECT pg_advisory_unlock(
                %s::integer,
                %s::integer
            ) AS released
            """,
            MIGRATION_LOCK_KEYS,
        )

        row = cursor.fetchone()

    if row is None or not bool(row["released"]):
        raise MigrationRunnerError(
            "Canonical database migration lock "
            "was not released."
        )


def table_exists(
    connection: psycopg.Connection,
    table_name: str,
) -> bool:
    with connection.cursor(
        row_factory=dict_row,
    ) as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = %s::text
            ) AS present
            """,
            (table_name,),
        )

        row = cursor.fetchone()

    return bool(row and row["present"])


def column_exists(
    connection: psycopg.Connection,
    *,
    table_name: str,
    column_name: str,
) -> bool:
    with connection.cursor(
        row_factory=dict_row,
    ) as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s::text
                  AND column_name = %s::text
            ) AS present
            """,
            (
                table_name,
                column_name,
            ),
        )

        row = cursor.fetchone()

    return bool(row and row["present"])



def validate_canonical_audit_schema(
    connection: psycopg.Connection,
) -> None:
    if not table_exists(
        connection,
        "schema_migration_audit",
    ):
        raise MigrationRunnerError(
            "Canonical migration audit table "
            "is missing."
        )

    with connection.cursor(
        row_factory=dict_row,
    ) as cursor:
        cursor.execute(
            """
            SELECT
                column_name,
                data_type,
                is_nullable
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name =
                    'schema_migration_audit'
            ORDER BY ordinal_position
            """
        )

        rows = cursor.fetchall()

    actual_columns = {
        str(row["column_name"]): (
            str(row["data_type"]),
            str(row["is_nullable"]),
        )
        for row in rows
    }

    expected_columns = {
        "version": ("text", "NO"),
        "source_sha256": ("text", "NO"),
        "execution_sha256": ("text", "NO"),
        "normalization_mode": ("text", "NO"),
        "applied_at": (
            "timestamp with time zone",
            "NO",
        ),
        "execution_duration_ms": (
            "bigint",
            "NO",
        ),
        "audit_mode": ("text", "NO"),
    }

    if actual_columns != expected_columns:
        raise MigrationRunnerError(
            "Canonical migration audit schema "
            "does not match migration 008."
        )

    legacy_column_name = (
        "content_" + "sha256"
    )

    if legacy_column_name in actual_columns:
        raise MigrationRunnerError(
            "A forbidden legacy migration-audit "
            "column is present."
        )



def load_applied_versions(
    connection: psycopg.Connection,
) -> dict[str, object]:
    if not table_exists(
        connection,
        "schema_migrations",
    ):
        return {}

    with connection.cursor(
        row_factory=dict_row,
    ) as cursor:
        cursor.execute(
            """
            SELECT
                version,
                applied_at
            FROM schema_migrations
            ORDER BY version
            """
        )

        return {
            str(row["version"]):
                row["applied_at"]
            for row in cursor.fetchall()
        }



def load_audit_rows(
    connection: psycopg.Connection,
) -> dict[str, dict[str, object]]:
    if not table_exists(
        connection,
        "schema_migration_audit",
    ):
        return {}

    validate_canonical_audit_schema(
        connection
    )

    with connection.cursor(
        row_factory=dict_row,
    ) as cursor:
        cursor.execute(
            """
            SELECT
                version,
                source_sha256,
                execution_sha256,
                normalization_mode,
                applied_at,
                execution_duration_ms,
                audit_mode
            FROM schema_migration_audit
            ORDER BY version
            """
        )

        return {
            str(row["version"]): dict(row)
            for row in cursor.fetchall()
        }



def validate_applied_prefix(
    *,
    migrations: Sequence[Migration],
    applied_versions: set[str],
) -> None:
    workspace_versions = [
        migration.version
        for migration in migrations
    ]

    unknown_versions = sorted(
        applied_versions
        - set(workspace_versions)
    )

    if unknown_versions:
        raise MigrationRunnerError(
            "Database contains unknown applied "
            "migrations: "
            + ", ".join(unknown_versions)
        )

    applied_positions = [
        index
        for index, version
        in enumerate(workspace_versions)
        if version in applied_versions
    ]

    if not applied_positions:
        return

    highest_position = max(
        applied_positions
    )

    required_prefix = set(
        workspace_versions[
            : highest_position + 1
        ]
    )

    missing_versions = sorted(
        required_prefix
        - applied_versions
    )

    if missing_versions:
        raise MigrationRunnerError(
            "Applied migration gap detected: "
            + ", ".join(missing_versions)
        )


def validate_audit_hashes(
    *,
    migrations: Sequence[Migration],
    audit_rows: dict[
        str,
        dict[str, object],
    ],
) -> None:
    migrations_by_version = {
        migration.version:
            migration
        for migration in migrations
    }

    unknown_versions = sorted(
        set(audit_rows)
        - set(migrations_by_version)
    )

    if unknown_versions:
        raise MigrationRunnerError(
            "Migration audit contains unknown "
            "versions: "
            + ", ".join(unknown_versions)
        )

    for version, row in audit_rows.items():
        migration = migrations_by_version[
            version
        ]

        if (
            str(row["source_sha256"])
            != migration.source_sha256
        ):
            raise MigrationRunnerError(
                "Applied migration source changed "
                "after execution: "
                f"{version}"
            )

        if (
            str(row["execution_sha256"])
            != migration.execution_sha256
        ):
            raise MigrationRunnerError(
                "Applied migration execution SQL "
                "changed after execution: "
                f"{version}"
            )

        if (
            str(row["normalization_mode"])
            != migration.normalization_mode
        ):
            raise MigrationRunnerError(
                "Applied migration normalization "
                "mode changed: "
                f"{version}"
            )


def require_complete_audit(
    *,
    migrations: Sequence[Migration],
    applied: dict[str, object],
    audit_rows: dict[
        str,
        dict[str, object],
    ],
) -> None:
    missing_versions = sorted(
        set(applied)
        - set(audit_rows)
    )

    if missing_versions:
        raise MigrationRunnerError(
            "Applied migrations have no immutable "
            "dual checksum audit: "
            + ", ".join(missing_versions)
            + ". Use "
            "--bootstrap-historical-audit only "
            "after validating the historical "
            "database state."
        )

    validate_audit_hashes(
        migrations=migrations,
        audit_rows=audit_rows,
    )



def insert_migration_audit_record(
    *,
    cursor: psycopg.Cursor,
    migration: Migration,
    applied_at: object,
    duration_ms: int,
    audit_mode: str,
) -> None:
    if audit_mode not in {
        "executed",
        "historical_bootstrap",
    }:
        raise MigrationRunnerError(
            "Unsupported migration audit mode: "
            f"{audit_mode}"
        )

    cursor.execute(
        """
        INSERT INTO schema_migration_audit (
            version,
            source_sha256,
            execution_sha256,
            normalization_mode,
            applied_at,
            execution_duration_ms,
            audit_mode
        )
        VALUES (
            %s::text,
            %s::text,
            %s::text,
            %s::text,
            %s::timestamptz,
            %s::bigint,
            %s::text
        )
        RETURNING
            source_sha256,
            execution_sha256,
            normalization_mode,
            audit_mode
        """,
        (
            migration.version,
            migration.source_sha256,
            migration.execution_sha256,
            migration.normalization_mode,
            applied_at,
            duration_ms,
            audit_mode,
        ),
    )

    persisted = cursor.fetchone()

    if persisted is None:
        raise MigrationRunnerError(
            "Could not create immutable "
            "migration audit: "
            f"{migration.version}"
        )

    if (
        str(persisted["source_sha256"])
        != migration.source_sha256
        or str(persisted["execution_sha256"])
        != migration.execution_sha256
        or str(persisted["normalization_mode"])
        != migration.normalization_mode
        or str(persisted["audit_mode"])
        != audit_mode
    ):
        raise MigrationRunnerError(
            "Persisted migration audit does not "
            "match the canonical execution "
            f"contract: {migration.version}"
        )


def inspect_migration_authority_state(
    *,
    connection: psycopg.Connection,
    migrations: Sequence[Migration],
) -> MigrationAuthorityInspection:
    version_table_present = table_exists(
        connection,
        "schema_migrations",
    )

    audit_table_present = table_exists(
        connection,
        "schema_migration_audit",
    )

    if (
        audit_table_present
        and not version_table_present
    ):
        return MigrationAuthorityInspection(
            state=(
                MigrationAuthorityState
                .INVALID_AUDIT_WITHOUT_VERSION_TABLE
            ),
            applied={},
            audit_rows={},
            version_table_present=False,
            audit_table_present=True,
        )

    applied = load_applied_versions(
        connection
    )

    try:
        validate_applied_prefix(
            migrations=migrations,
            applied_versions=set(applied),
        )
    except MigrationRunnerError as error:
        message = str(error).lower()

        if "unknown applied migrations" in message:
            state = (
                MigrationAuthorityState
                .UNKNOWN_APPLIED_VERSION
            )
        else:
            state = (
                MigrationAuthorityState
                .APPLIED_VERSION_GAP
            )

        return MigrationAuthorityInspection(
            state=state,
            applied=applied,
            audit_rows={},
            version_table_present=(
                version_table_present
            ),
            audit_table_present=(
                audit_table_present
            ),
        )

    migration_008_applied = (
        AUDIT_AUTHORITY_MIGRATION_VERSION
        in applied
    )

    if (
        migration_008_applied
        and not audit_table_present
    ):
        return MigrationAuthorityInspection(
            state=(
                MigrationAuthorityState
                .INVALID_008_APPLIED_WITHOUT_AUDIT_TABLE
            ),
            applied=applied,
            audit_rows={},
            version_table_present=(
                version_table_present
            ),
            audit_table_present=False,
        )

    if (
        audit_table_present
        and not migration_008_applied
    ):
        return MigrationAuthorityInspection(
            state=(
                MigrationAuthorityState
                .INVALID_AUDIT_TABLE_WITHOUT_008_VERSION
            ),
            applied=applied,
            audit_rows={},
            version_table_present=(
                version_table_present
            ),
            audit_table_present=True,
        )

    if not audit_table_present:
        state = (
            MigrationAuthorityState
            .HISTORICAL_UNAUDITED
            if applied
            else
            MigrationAuthorityState
            .EMPTY_UNBOOTSTRAPPED
        )

        return MigrationAuthorityInspection(
            state=state,
            applied=applied,
            audit_rows={},
            version_table_present=(
                version_table_present
            ),
            audit_table_present=False,
        )

    try:
        audit_rows = load_audit_rows(
            connection
        )

        validate_audit_hashes(
            migrations=migrations,
            audit_rows=audit_rows,
        )
    except MigrationRunnerError:
        return MigrationAuthorityInspection(
            state=(
                MigrationAuthorityState
                .BOOTSTRAPPED_HASH_MISMATCH
            ),
            applied=applied,
            audit_rows={},
            version_table_present=True,
            audit_table_present=True,
        )

    if set(applied) != set(audit_rows):
        return MigrationAuthorityInspection(
            state=(
                MigrationAuthorityState
                .BOOTSTRAPPED_INCOMPLETE_AUDIT
            ),
            applied=applied,
            audit_rows=audit_rows,
            version_table_present=True,
            audit_table_present=True,
        )

    return MigrationAuthorityInspection(
        state=(
            MigrationAuthorityState
            .BOOTSTRAPPED_VALID
        ),
        applied=applied,
        audit_rows=audit_rows,
        version_table_present=True,
        audit_table_present=True,
    )


def build_migration_plan(
    *,
    migrations: Sequence[Migration],
    inspection: MigrationAuthorityInspection,
    bootstrap_historical_audit_enabled: bool,
) -> dict[str, object]:
    migrations_by_version = {
        migration.version: migration
        for migration in migrations
    }

    if (
        AUDIT_AUTHORITY_MIGRATION_VERSION
        not in migrations_by_version
    ):
        raise MigrationRunnerError(
            "Canonical audit authority migration "
            "008 is missing from the workspace."
        )

    invalid_states = {
        MigrationAuthorityState
        .INVALID_AUDIT_WITHOUT_VERSION_TABLE,
        MigrationAuthorityState
        .INVALID_008_APPLIED_WITHOUT_AUDIT_TABLE,
        MigrationAuthorityState
        .INVALID_AUDIT_TABLE_WITHOUT_008_VERSION,
        MigrationAuthorityState
        .BOOTSTRAPPED_INCOMPLETE_AUDIT,
        MigrationAuthorityState
        .BOOTSTRAPPED_HASH_MISMATCH,
        MigrationAuthorityState
        .UNKNOWN_APPLIED_VERSION,
        MigrationAuthorityState
        .APPLIED_VERSION_GAP,
    }

    if inspection.state in invalid_states:
        raise MigrationRunnerError(
            "Database migration authority state "
            "is invalid and cannot be repaired "
            "automatically: "
            f"{inspection.state.value}"
        )

    if (
        inspection.state
        == MigrationAuthorityState
        .BOOTSTRAPPED_VALID
        and bootstrap_historical_audit_enabled
    ):
        raise MigrationRunnerError(
            "--bootstrap-historical-audit is "
            "forbidden after migration 008 has "
            "established canonical audit authority."
        )

    if (
        inspection.state
        == MigrationAuthorityState
        .HISTORICAL_UNAUDITED
        and not bootstrap_historical_audit_enabled
    ):
        raise MigrationRunnerError(
            "Historical migrations are applied "
            "without canonical audit authority. "
            "Use --bootstrap-historical-audit "
            "only after external validation of "
            "the historical database state."
        )

    applied = set(inspection.applied)

    audit_index = next(
        index
        for index, migration
        in enumerate(migrations)
        if (
            migration.version
            == AUDIT_AUTHORITY_MIGRATION_VERSION
        )
    )

    authority_prefix = list(
        migrations[: audit_index + 1]
    )

    bootstrap_pending = [
        migration
        for migration in authority_prefix
        if migration.version not in applied
    ]

    post_bootstrap_pending = [
        migration
        for migration
        in migrations[audit_index + 1:]
        if migration.version not in applied
    ]

    if (
        inspection.state
        == MigrationAuthorityState
        .BOOTSTRAPPED_VALID
    ):
        bootstrap_mode = "none"
        historical_versions: list[str] = []
        executed_bootstrap_versions: list[str] = []

    elif (
        inspection.state
        == MigrationAuthorityState
        .EMPTY_UNBOOTSTRAPPED
    ):
        bootstrap_mode = "fresh"
        historical_versions = []
        executed_bootstrap_versions = [
            migration.version
            for migration in bootstrap_pending
        ]

    else:
        bootstrap_mode = "historical"
        historical_versions = [
            migration.version
            for migration in authority_prefix
            if migration.version in applied
        ]
        executed_bootstrap_versions = [
            migration.version
            for migration in bootstrap_pending
        ]

    return {
        "authority_state":
            inspection.state.value,
        "bootstrap_mode":
            bootstrap_mode,
        "operator_consent_required": (
            inspection.state
            == MigrationAuthorityState
            .HISTORICAL_UNAUDITED
        ),
        "operator_consent_present":
            bootstrap_historical_audit_enabled,
        "bootstrap_pending":
            bootstrap_pending,
        "post_bootstrap_pending":
            post_bootstrap_pending,
        "historical_audit_versions":
            historical_versions,
        "executed_bootstrap_versions":
            executed_bootstrap_versions,
    }


def execute_audit_authority_bootstrap(
    *,
    connection: psycopg.Connection,
    migrations: Sequence[Migration],
    inspection: MigrationAuthorityInspection,
    plan: dict[str, object],
) -> list[MigrationExecutionRecord]:
    bootstrap_pending = list(
        plan["bootstrap_pending"]
    )

    if not bootstrap_pending:
        raise MigrationRunnerError(
            "Audit-authority bootstrap was "
            "requested without pending authority "
            "migrations."
        )

    migrations_by_version = {
        migration.version: migration
        for migration in migrations
    }

    executed_records: list[
        MigrationExecutionRecord
    ] = []

    with connection.transaction():
        with connection.cursor(
            row_factory=dict_row,
        ) as cursor:
            applied_at_by_version = dict(
                inspection.applied
            )

            for migration in bootstrap_pending:
                started_at = time.monotonic()

                cursor.execute(
                    migration.execution_sql
                )

                cursor.execute(
                    """
                    SELECT
                        version,
                        applied_at
                    FROM schema_migrations
                    WHERE version = %s::text
                    """,
                    (
                        migration.version,
                    ),
                )

                applied_row = cursor.fetchone()

                if applied_row is None:
                    raise MigrationRunnerError(
                        "Migration did not record "
                        "its own version: "
                        f"{migration.version}"
                    )

                duration_ms = max(
                    0,
                    round(
                        (
                            time.monotonic()
                            - started_at
                        )
                        * 1000
                    ),
                )

                applied_at = (
                    applied_row["applied_at"]
                )

                applied_at_by_version[
                    migration.version
                ] = applied_at

                executed_records.append(
                    MigrationExecutionRecord(
                        version=migration.version,
                        source_sha256=(
                            migration.source_sha256
                        ),
                        execution_sha256=(
                            migration.execution_sha256
                        ),
                        normalization_mode=(
                            migration.normalization_mode
                        ),
                        duration_ms=duration_ms,
                        applied_at=applied_at,
                    )
                )

            if not table_exists(
                connection,
                "schema_migration_audit",
            ):
                raise MigrationRunnerError(
                    "Migration 008 did not create "
                    "canonical audit authority."
                )

            validate_canonical_audit_schema(
                connection
            )

            executed_by_version = {
                record.version: record
                for record in executed_records
            }

            audit_index = next(
                index
                for index, migration
                in enumerate(migrations)
                if (
                    migration.version
                    == AUDIT_AUTHORITY_MIGRATION_VERSION
                )
            )

            authority_versions = [
                migration.version
                for migration
                in migrations[: audit_index + 1]
            ]

            for version in authority_versions:
                migration = migrations_by_version[
                    version
                ]

                if version not in applied_at_by_version:
                    raise MigrationRunnerError(
                        "Bootstrap did not produce "
                        "a complete authority prefix: "
                        f"{version}"
                    )

                record = executed_by_version.get(
                    version
                )

                if record is None:
                    audit_mode = (
                        "historical_bootstrap"
                    )
                    duration_ms = 0
                else:
                    audit_mode = "executed"
                    duration_ms = (
                        record.duration_ms
                    )

                insert_migration_audit_record(
                    cursor=cursor,
                    migration=migration,
                    applied_at=(
                        applied_at_by_version[
                            version
                        ]
                    ),
                    duration_ms=duration_ms,
                    audit_mode=audit_mode,
                )

            cursor.execute(
                """
                SELECT
                    COUNT(*)::bigint AS count
                FROM schema_migration_audit
                """
            )

            count_row = cursor.fetchone()

            expected_count = len(
                authority_versions
            )

            actual_count = int(
                count_row["count"]
                if count_row
                else -1
            )

            if actual_count != expected_count:
                raise MigrationRunnerError(
                    "Bootstrap audit row count "
                    "does not match the complete "
                    "authority prefix."
                )

    return executed_records




def apply_one_migration(
    *,
    connection: psycopg.Connection,
    migration: Migration,
) -> int:
    started_at = time.monotonic()

    with connection.transaction():
        with connection.cursor(
            row_factory=dict_row,
        ) as cursor:
            cursor.execute(
                migration.execution_sql
            )

            cursor.execute(
                """
                SELECT
                    version,
                    applied_at
                FROM schema_migrations
                WHERE version = %s::text
                """,
                (
                    migration.version,
                ),
            )

            applied_row = cursor.fetchone()

            if applied_row is None:
                raise MigrationRunnerError(
                    "Migration did not record "
                    "its own version: "
                    f"{migration.version}"
                )

            duration_ms = max(
                0,
                round(
                    (
                        time.monotonic()
                        - started_at
                    )
                    * 1000
                ),
            )

            insert_migration_audit_record(
                cursor=cursor,
                migration=migration,
                applied_at=(
                    applied_row["applied_at"]
                ),
                duration_ms=duration_ms,
                audit_mode="executed",
            )

    return duration_ms




def inspect_state(
    *,
    connection: psycopg.Connection,
    migrations: Sequence[Migration],
) -> tuple[
    dict[str, object],
    dict[str, dict[str, object]],
]:
    inspection = (
        inspect_migration_authority_state(
            connection=connection,
            migrations=migrations,
        )
    )

    if (
        inspection.state
        != MigrationAuthorityState
        .BOOTSTRAPPED_VALID
    ):
        raise MigrationRunnerError(
            "Canonical migration authority is "
            "not in the bootstrapped-valid state: "
            f"{inspection.state.value}"
        )

    return (
        inspection.applied,
        inspection.audit_rows,
    )




def run_migrations(
    *,
    database_url: str,
    migration_root: Path,
    dry_run: bool,
    bootstrap_historical_audit_enabled: bool,
    lock_timeout_seconds: float,
    statement_timeout_ms: int,
    idle_transaction_timeout_ms: int,
) -> dict[str, object]:
    validate_timeout_values(
        lock_timeout_seconds=(
            lock_timeout_seconds
        ),
        statement_timeout_ms=(
            statement_timeout_ms
        ),
        idle_transaction_timeout_ms=(
            idle_transaction_timeout_ms
        ),
    )

    migrations = discover_migrations(
        migration_root
    )

    with psycopg.connect(
        database_url,
        connect_timeout=10,
        autocommit=True,
        row_factory=dict_row,
    ) as connection:
        configure_session(
            connection,
            statement_timeout_ms=(
                statement_timeout_ms
            ),
            idle_transaction_timeout_ms=(
                idle_transaction_timeout_ms
            ),
        )

        acquire_migration_lock(
            connection,
            timeout_seconds=(
                lock_timeout_seconds
            ),
        )

        primary_error: BaseException | None = None

        try:
            inspection_before = (
                inspect_migration_authority_state(
                    connection=connection,
                    migrations=migrations,
                )
            )

            plan = build_migration_plan(
                migrations=migrations,
                inspection=inspection_before,
                bootstrap_historical_audit_enabled=(
                    bootstrap_historical_audit_enabled
                ),
            )

            bootstrap_pending = list(
                plan["bootstrap_pending"]
            )

            post_bootstrap_pending = list(
                plan["post_bootstrap_pending"]
            )

            pending_versions = [
                migration.version
                for migration in (
                    bootstrap_pending
                    + post_bootstrap_pending
                )
            ]

            if dry_run:
                return {
                    "status": "dry_run",
                    "authority_state":
                        plan["authority_state"],
                    "bootstrap_mode":
                        plan["bootstrap_mode"],
                    "operator_consent_required":
                        plan[
                            "operator_consent_required"
                        ],
                    "operator_consent_present":
                        plan[
                            "operator_consent_present"
                        ],
                    "workspace_versions": [
                        migration.version
                        for migration in migrations
                    ],
                    "applied_before": sorted(
                        inspection_before.applied
                    ),
                    "pending":
                        pending_versions,
                    "historical_audit_versions":
                        list(
                            plan[
                                "historical_audit_versions"
                            ]
                        ),
                    "executed_audit_versions":
                        list(
                            plan[
                                "executed_bootstrap_versions"
                            ]
                        )
                        + [
                            migration.version
                            for migration
                            in post_bootstrap_pending
                        ],
                    "executed": [],
                    "database_mutated": False,
                    "normalization": {
                        migration.version:
                            migration.normalization_mode
                        for migration in migrations
                    },
                }

            executed: list[
                dict[str, object]
            ] = []

            if (
                plan["bootstrap_mode"]
                != "none"
            ):
                bootstrap_records = (
                    execute_audit_authority_bootstrap(
                        connection=connection,
                        migrations=migrations,
                        inspection=inspection_before,
                        plan=plan,
                    )
                )

                executed.extend(
                    {
                        "version": record.version,
                        "source_sha256":
                            record.source_sha256,
                        "execution_sha256":
                            record.execution_sha256,
                        "normalization_mode":
                            record.normalization_mode,
                        "duration_ms":
                            record.duration_ms,
                    }
                    for record
                    in bootstrap_records
                )

            inspection_after_bootstrap = (
                inspect_migration_authority_state(
                    connection=connection,
                    migrations=migrations,
                )
            )

            if (
                inspection_after_bootstrap.state
                != MigrationAuthorityState
                .BOOTSTRAPPED_VALID
            ):
                raise MigrationRunnerError(
                    "Migration audit authority "
                    "bootstrap did not produce a "
                    "valid canonical state: "
                    f"{inspection_after_bootstrap.state.value}"
                )

            for migration in post_bootstrap_pending:
                duration_ms = apply_one_migration(
                    connection=connection,
                    migration=migration,
                )

                executed.append(
                    {
                        "version":
                            migration.version,
                        "source_sha256":
                            migration.source_sha256,
                        "execution_sha256":
                            migration.execution_sha256,
                        "normalization_mode":
                            migration.normalization_mode,
                        "duration_ms":
                            duration_ms,
                    }
                )

            inspection_after = (
                inspect_migration_authority_state(
                    connection=connection,
                    migrations=migrations,
                )
            )

            if (
                inspection_after.state
                != MigrationAuthorityState
                .BOOTSTRAPPED_VALID
            ):
                raise MigrationRunnerError(
                    "Final migration authority "
                    "state is invalid: "
                    f"{inspection_after.state.value}"
                )

            expected_versions = {
                migration.version
                for migration in migrations
            }

            if (
                set(inspection_after.applied)
                != expected_versions
            ):
                raise MigrationRunnerError(
                    "Database migration state "
                    "does not match the canonical "
                    "workspace migration set."
                )

            if (
                set(inspection_after.audit_rows)
                != expected_versions
            ):
                raise MigrationRunnerError(
                    "Migration audit state "
                    "does not match the canonical "
                    "workspace migration set."
                )

            return {
                "status": (
                    "migrated"
                    if executed
                    else "noop"
                ),
                "authority_state":
                    inspection_after.state.value,
                "bootstrap_mode":
                    plan["bootstrap_mode"],
                "workspace_versions": [
                    migration.version
                    for migration in migrations
                ],
                "applied_before": sorted(
                    inspection_before.applied
                ),
                "pending":
                    pending_versions,
                "executed":
                    executed,
                "applied_after": sorted(
                    inspection_after.applied
                ),
                "database_mutated": bool(
                    executed
                ),
            }

        except BaseException as error:
            primary_error = error
            raise

        finally:
            try:
                release_migration_lock(
                    connection
                )
            except BaseException as release_error:
                if primary_error is None:
                    raise

                primary_error.add_note(
                    "Additional migration-lock "
                    "release failure: "
                    f"{release_error}"
                )



def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run STAY PostgreSQL migrations "
            "through the single canonical "
            "migration authority."
        )
    )

    parser.add_argument(
        "--database-url",
        default=(
            os.getenv("DATABASE_URL")
            or ""
        ),
    )

    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=(
            Path(__file__)
            .resolve()
            .parents[1]
            / "migrations"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--bootstrap-historical-audit",
        action="store_true",
    )

    parser.add_argument(
        "--lock-timeout-seconds",
        type=float,
        default=(
            DEFAULT_LOCK_TIMEOUT_SECONDS
        ),
    )

    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=(
            DEFAULT_STATEMENT_TIMEOUT_MS
        ),
    )

    parser.add_argument(
        "--idle-transaction-timeout-ms",
        type=int,
        default=(
            DEFAULT_IDLE_TRANSACTION_TIMEOUT_MS
        ),
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    database_url = str(
        arguments.database_url
    ).strip()

    if not database_url:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error":
                        "DATABASE_URL is missing.",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )

        return 2

    try:
        result = run_migrations(
            database_url=database_url,
            migration_root=(
                arguments.migrations_dir
            ),
            dry_run=arguments.dry_run,
            bootstrap_historical_audit_enabled=(
                arguments
                .bootstrap_historical_audit
            ),
            lock_timeout_seconds=(
                arguments
                .lock_timeout_seconds
            ),
            statement_timeout_ms=(
                arguments
                .statement_timeout_ms
            ),
            idle_transaction_timeout_ms=(
                arguments
                .idle_transaction_timeout_ms
            ),
        )

    except (
        MigrationRunnerError,
        psycopg.Error,
        OSError,
        ValueError,
    ) as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(error),
                    "notes": list(
                        getattr(
                            error,
                            "__notes__",
                            [],
                        )
                    ),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )

        return 1

    print(
        json.dumps(
            result,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
