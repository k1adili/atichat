import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import Database
from main import _acquire_instance_lock, _portable_data_dir


class ApplicationPathTests(unittest.TestCase):
    def test_existing_profile_and_identity_are_migrated_to_portable_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            application_dir = root / "portable"
            legacy_dir = root / "appdata"
            legacy_dir.mkdir()
            legacy_database = Database(legacy_dir / "k1chat.db")
            profile = legacy_database.get_profile()
            legacy_database.save_profile(
                profile["user_id"], "Portable user", "avatar-data", False
            )
            (legacy_dir / "peer-cert.pem").write_text("certificate", encoding="ascii")
            (legacy_dir / "peer-key.pem").write_text("private-key", encoding="ascii")

            data_dir = _portable_data_dir(application_dir, legacy_dir)

            migrated = Database(data_dir / "k1chat.db").get_profile()
            self.assertEqual(migrated["display_name"], "Portable user")
            self.assertEqual(migrated["avatar"], "avatar-data")
            self.assertEqual((data_dir / "peer-cert.pem").read_text("ascii"), "certificate")
            self.assertEqual((data_dir / "peer-key.pem").read_text("ascii"), "private-key")

    def test_portable_database_is_not_overwritten_on_later_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            application_dir = root / "portable"
            legacy_dir = root / "appdata"
            legacy_dir.mkdir()
            portable_data = application_dir / "data"
            portable_database = Database(portable_data / "k1chat.db")
            profile = portable_database.get_profile()
            portable_database.save_profile(
                profile["user_id"], "Portable profile", "", False
            )
            legacy_database = Database(legacy_dir / "k1chat.db")
            legacy_profile = legacy_database.get_profile()
            legacy_database.save_profile(
                legacy_profile["user_id"], "Old profile", "", False
            )

            data_dir = _portable_data_dir(application_dir, legacy_dir)

            self.assertEqual(
                Database(data_dir / "k1chat.db").get_profile()["display_name"],
                "Portable profile",
            )

    def test_portable_database_migration_uses_sqlite_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_dir = root / "appdata"
            legacy_dir.mkdir()
            connection = sqlite3.connect(legacy_dir / "k1chat.db")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("CREATE TABLE migration_probe (value TEXT)")
            connection.execute("INSERT INTO migration_probe VALUES ('complete')")
            connection.commit()
            connection.close()

            data_dir = _portable_data_dir(root / "portable", legacy_dir)

            migrated = sqlite3.connect(data_dir / "k1chat.db")
            try:
                self.assertEqual(
                    migrated.execute("SELECT value FROM migration_probe").fetchone()[0],
                    "complete",
                )
            finally:
                migrated.close()

    def test_instance_lock_prevents_second_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = _acquire_instance_lock(Path(temporary))
            self.assertTrue(first.isLocked())
            with self.assertRaises(RuntimeError):
                _acquire_instance_lock(Path(temporary))
            first.unlock()
            second = _acquire_instance_lock(Path(temporary))
            self.assertTrue(second.isLocked())
            second.unlock()


if __name__ == "__main__":
    unittest.main()
