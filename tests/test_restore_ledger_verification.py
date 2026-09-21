from unittest.mock import MagicMock, patch

import pytest

from app.services.deletion_retention_service import DeletionRetentionService


def test_no_conflicts_does_not_prove_current_independent_ledger():
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = (False,)
    with patch('app.services.deletion_retention_service.psycopg.connect') as connect:
        connect.return_value.__enter__.return_value = connection
        with pytest.raises(RuntimeError, match='independent deletion ledger cannot be verified'):
            DeletionRetentionService(database_url='test-only').assert_restore_clean()
