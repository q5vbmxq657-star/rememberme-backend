from __future__ import annotations

import ast
import importlib.util
import shutil
import sys
import tomllib
from pathlib import Path

import pytest


RUNNER_PATH = Path(
    "scripts/run_database_migrations.py"
)

REPOSITORY_PATH = Path(
    "app/services/"
    "digital_human_profile_repository.py"
)

RAILWAY_CONFIG_PATH = Path(
    "railway.toml"
)

PROCFILE_PATH = Path(
    "Procfile"
)


def load_runner_module():
    module_name = (
        "stay_database_migration_runner_test"
    )

    specification = (
        importlib.util.spec_from_file_location(
            module_name,
            RUNNER_PATH,
        )
    )

    assert specification is not None
    assert specification.loader is not None

    module = (
        importlib.util.module_from_spec(
            specification
        )
    )

    sys.modules[module_name] = module

    try:
        specification.loader.exec_module(
            module
        )
    finally:
        sys.modules.pop(
            module_name,
            None,
        )

    return module


def test_repository_has_no_parallel_runner():
    source = REPOSITORY_PATH.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    repository = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name
        == "DigitalHumanProfileRepository"
    )

    assert not any(
        isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == "apply_migration"
        for node in repository.body
    )




def test_legacy_migrations_are_exactly_frozen():
    module = load_runner_module()

    migrations = module.discover_migrations(
        Path("migrations")
    )

    assert [
        migration.version
        for migration in migrations
    ] == [
        "002_digital_human_profiles",
        "003_avatar_evidence_assets",
        "004_identity_verification_receipts",
        "005_generated_preview_jobs",
        "006_profile_erasure_requests",
        "007_profile_erasure_resume_authority",
        "008_migration_audit_authority",
        "009_pgvector_memory_authority",
        "010_memory_transaction_operations",
        "011_user_identity_profile_memberships",
        "012_user_sessions",
        "013_apple_provider_credentials",
    ]

    for migration in migrations[:6]:
        assert (
            migration.source_sha256
            == module.LEGACY_MIGRATION_SOURCE_HASHES[
                migration.version
            ]
        )

        assert (
            migration.execution_sha256
            == module.LEGACY_MIGRATION_EXECUTION_HASHES[
                migration.version
            ]
        )

        assert (
            migration.normalization_mode
            == module
            .NORMALIZATION_LEGACY_OUTER_TRANSACTION_REMOVED
        )

    migration_008 = next(
        migration
        for migration in migrations
        if migration.version
        == "008_migration_audit_authority"
    )

    migration_009 = next(
        migration
        for migration in migrations
        if migration.version
        == "009_pgvector_memory_authority"
    )

    assert (
        migration_008.normalization_mode
        == module.NORMALIZATION_NONE
    )

    assert (
        migration_008.source_sha256
        == migration_008.execution_sha256
    )

    assert (
        migration_009.normalization_mode
        == module.NORMALIZATION_NONE
    )

    assert (
        migration_009.source_sha256
        == migration_009.execution_sha256
    )




def test_normalization_preserves_plpgsql_bodies():
    module = load_runner_module()

    migrations = module.discover_migrations(
        Path("migrations")
    )

    for migration in migrations:
        source = migration.path.read_text(
            encoding="utf-8"
        )

        source_bodies = (
            module.dollar_quoted_bodies(
                module.lex_sql(source)
            )
        )

        execution_bodies = (
            module.dollar_quoted_bodies(
                module.lex_sql(
                    migration.execution_sql
                )
            )
        )

        assert source_bodies == execution_bodies


def test_normalized_sql_has_no_outer_wrapper():
    module = load_runner_module()

    for migration in module.discover_migrations(
        Path("migrations")
    ):
        assert not (
            module.has_outer_transaction_wrapper(
                migration.execution_sql
            )
        )


def test_future_outer_wrapper_is_blocked(
    tmp_path: Path,
):
    module = load_runner_module()

    migration_root = (
        tmp_path
        / "migrations"
    )

    migration_root.mkdir()

    for source_path in sorted(
        Path("migrations").glob("*.sql")
    ):
        shutil.copy2(
            source_path,
            migration_root
            / source_path.name,
        )

    (
        migration_root
        / "010_future_wrapper.sql"
    ).write_text(
        """
BEGIN;

CREATE TABLE forbidden_future_wrapper (
    id INTEGER PRIMARY KEY
);

INSERT INTO schema_migrations (
    version
)
VALUES (
    '010_future_wrapper'
);

COMMIT;
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(
        module.MigrationRunnerError,
        match=(
            "Future migration contains "
            "an outer transaction wrapper"
        ),
    ):
        module.discover_migrations(
            migration_root
        )


def test_future_wrapper_free_migration_is_allowed(
    tmp_path: Path,
):
    module = load_runner_module()

    migration_root = (
        tmp_path
        / "migrations"
    )

    migration_root.mkdir()

    for source_path in sorted(
        Path("migrations").glob("*.sql")
    ):
        shutil.copy2(
            source_path,
            migration_root
            / source_path.name,
        )

    (
        migration_root
        / "014_future_clean.sql"
    ).write_text(
        """
CREATE TABLE future_clean (
    id INTEGER PRIMARY KEY
);

INSERT INTO schema_migrations (
    version
)
VALUES (
        '014_future_clean'
);
""".lstrip(),
        encoding="utf-8",
    )

    migrations = (
        module.discover_migrations(
            migration_root
        )
    )

    migration = migrations[-1]

    assert (
        migration.version
            == "014_future_clean"
    )

    assert (
        migration.normalization_mode
        == module.NORMALIZATION_NONE
    )

    assert (
        migration.source_sha256
        == migration.execution_sha256
    )


def test_legacy_hash_drift_is_blocked(
    tmp_path: Path,
):
    module = load_runner_module()

    migration_root = (
        tmp_path
        / "migrations"
    )

    shutil.copytree(
        Path("migrations"),
        migration_root,
    )

    path = (
        migration_root
        / "002_digital_human_profiles.sql"
    )

    path.write_text(
        path.read_text(
            encoding="utf-8"
        )
        + "\n-- unauthorized change\n",
        encoding="utf-8",
    )

    with pytest.raises(
        module.MigrationRunnerError,
        match=(
            "Legacy migration source "
            "hash mismatch"
        ),
    ):
        module.discover_migrations(
            migration_root
        )


def test_dual_audit_contract_is_present():
    source = RUNNER_PATH.read_text(
        encoding="utf-8"
    )

    for token in {
        "source_sha256",
        "execution_sha256",
        "normalization_mode",
        "legacy_outer_transaction_removed",
    }:
        assert token in source


def test_primary_error_priority_is_preserved():
    source = RUNNER_PATH.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "run_migrations"
    )

    add_note_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(
            node.func,
            ast.Attribute,
        )
        and node.func.attr == "add_note"
        and isinstance(
            node.func.value,
            ast.Name,
        )
        and node.func.value.id
        == "primary_error"
    ]

    assert len(add_note_calls) == 1

def test_migration_008_is_canonical_audit_authority():
    source = (
        Path("migrations")
        / "008_migration_audit_authority.sql"
    ).read_text(
        encoding="utf-8"
    )

    assert (
        source.count(
            "CREATE TABLE "
            "schema_migration_audit"
        )
        == 1
    )

    assert (
        source.count(
            "INSERT INTO schema_migrations"
        )
        == 1
    )

    assert (
        "INSERT INTO schema_migration_audit"
        not in source
    )

    assert "content_sha256" not in source
    assert "BEGIN;" not in source
    assert "COMMIT;" not in source


def test_runner_has_no_runtime_schema_ddl():
    source = RUNNER_PATH.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    function_names = {
        node.name
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    }

    assert (
        "ensure_authority_tables"
        not in function_names
    )

    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "CREATE EXTENSION" not in source
    assert "CREATE INDEX" not in source


def test_authority_state_machine_has_ten_states():
    module = load_runner_module()

    assert len(
        module.MigrationAuthorityState
    ) == 10

    assert {
        state.value
        for state
        in module.MigrationAuthorityState
    } == {
        "empty_unbootstrapped",
        "historical_unaudited",
        "invalid_audit_without_version_table",
        "invalid_008_applied_without_audit_table",
        "invalid_audit_table_without_008_version",
        "bootstrapped_valid",
        "bootstrapped_incomplete_audit",
        "bootstrapped_hash_mismatch",
        "unknown_applied_version",
        "applied_version_gap",
    }


def test_historical_plan_requires_operator_consent():
    module = load_runner_module()

    migrations = module.discover_migrations(
        Path("migrations")
    )

    inspection = (
        module.MigrationAuthorityInspection(
            state=(
                module.MigrationAuthorityState
                .HISTORICAL_UNAUDITED
            ),
            applied={
                "002_digital_human_profiles":
                    object(),
                "003_avatar_evidence_assets":
                    object(),
            },
            audit_rows={},
            version_table_present=True,
            audit_table_present=False,
        )
    )

    with pytest.raises(
        module.MigrationRunnerError,
        match="bootstrap-historical-audit",
    ):
        module.build_migration_plan(
            migrations=migrations,
            inspection=inspection,
            bootstrap_historical_audit_enabled=False,
        )

    plan = module.build_migration_plan(
        migrations=migrations,
        inspection=inspection,
        bootstrap_historical_audit_enabled=True,
    )

    assert plan["bootstrap_mode"] == "historical"

    assert plan[
        "historical_audit_versions"
    ] == [
        "002_digital_human_profiles",
        "003_avatar_evidence_assets",
    ]

    assert plan[
        "executed_bootstrap_versions"
    ] == [
        "004_identity_verification_receipts",
        "005_generated_preview_jobs",
        "006_profile_erasure_requests",
        "007_profile_erasure_resume_authority",
        "008_migration_audit_authority",
    ]



def test_fresh_plan_bootstraps_through_008():
    module = load_runner_module()

    migrations = module.discover_migrations(
        Path("migrations")
    )

    inspection = (
        module.MigrationAuthorityInspection(
            state=(
                module.MigrationAuthorityState
                .EMPTY_UNBOOTSTRAPPED
            ),
            applied={},
            audit_rows={},
            version_table_present=False,
            audit_table_present=False,
        )
    )

    plan = module.build_migration_plan(
        migrations=migrations,
        inspection=inspection,
        bootstrap_historical_audit_enabled=False,
    )

    assert plan["bootstrap_mode"] == "fresh"

    assert [
        migration.version
        for migration
        in plan["bootstrap_pending"]
    ] == [
        "002_digital_human_profiles",
        "003_avatar_evidence_assets",
        "004_identity_verification_receipts",
        "005_generated_preview_jobs",
        "006_profile_erasure_requests",
        "007_profile_erasure_resume_authority",
        "008_migration_audit_authority",
    ]

    assert [
        migration.version
        for migration
        in plan["post_bootstrap_pending"]
    ] == [
        "009_pgvector_memory_authority",
        "010_memory_transaction_operations",
        "011_user_identity_profile_memberships",
        "012_user_sessions",
        "013_apple_provider_credentials",
    ]



def test_historical_flag_is_rejected_after_008():
    module = load_runner_module()

    migrations = module.discover_migrations(
        Path("migrations")
    )

    inspection = (
        module.MigrationAuthorityInspection(
            state=(
                module.MigrationAuthorityState
                .BOOTSTRAPPED_VALID
            ),
            applied={
                migration.version: object()
                for migration in migrations
            },
            audit_rows={
                migration.version: {}
                for migration in migrations
            },
            version_table_present=True,
            audit_table_present=True,
        )
    )

    with pytest.raises(
        module.MigrationRunnerError,
        match="forbidden after migration 008",
    ):
        module.build_migration_plan(
            migrations=migrations,
            inspection=inspection,
            bootstrap_historical_audit_enabled=True,
        )

def test_audit_authority_boundary_uses_canonical_version():
    source = RUNNER_PATH.read_text(
        encoding="utf-8"
    )

    assert (
        "migration.numeric_version <= 8"
        not in source
    )

    assert (
        "AUDIT_AUTHORITY_MIGRATION_VERSION"
        in source
    )

    assert (
        "migrations[: audit_index + 1]"
        in source
    )


def test_railway_runs_canonical_migrations_before_service_deploy():
    configuration = tomllib.loads(
        RAILWAY_CONFIG_PATH.read_text(
            encoding="utf-8"
        )
    )

    assert configuration["deploy"][
        "preDeployCommand"
    ] == (
        "python "
        "scripts/run_database_migrations.py"
    )

    procfile = PROCFILE_PATH.read_text(
        encoding="utf-8"
    )

    assert "run_database_migrations.py" not in procfile
