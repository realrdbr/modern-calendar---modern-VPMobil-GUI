from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import admin
import main
from cli_runtime import uses_internal_mariadb


class RuntimeFallbackTests(unittest.TestCase):
    def test_uses_internal_mariadb_detects_compose_host(self):
        with patch.dict("os.environ", {"DB_HOST": "mariadb"}, clear=False):
            self.assertTrue(uses_internal_mariadb())

    def test_build_store_falls_back_to_sqlite_on_host_for_internal_mariadb(self):
        expected = Path("/tmp/fallback.sqlite3")
        with patch.dict(
            "os.environ",
            {
                "DB_HOST": "mariadb",
                "DB_USER": "cal11user",
                "DB_NAME": "cal11",
                "APP_DATABASE": str(expected),
                "APP_ENCRYPTION_KEY": "test-key",
                "APP_DATABASE_URL": "",
            },
            clear=False,
        ), patch("main.running_in_container", return_value=False), patch("main.AccountStore") as account_store, patch("main.log"):
            main.build_store()
        account_store.assert_called_once_with(expected, "test-key")

    def test_admin_store_falls_back_to_sqlite_on_host_for_internal_mariadb(self):
        expected = Path("/tmp/admin-fallback.sqlite3")
        with patch.dict(
            "os.environ",
            {
                "DB_HOST": "mariadb",
                "DB_USER": "cal11user",
                "DB_NAME": "cal11",
                "APP_DATABASE": str(expected),
                "APP_ENCRYPTION_KEY": "test-key",
                "APP_DATABASE_URL": "",
            },
            clear=False,
        ), patch("admin.running_in_container", return_value=False), patch("admin.AccountStore") as account_store:
            admin.store()
        account_store.assert_called_once_with(expected, "test-key")


if __name__ == "__main__":
    unittest.main()
