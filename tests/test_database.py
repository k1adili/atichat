import tempfile
import unittest
from pathlib import Path

from database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "test.db")
        self.profile = self.database.get_profile()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_profile_is_created_and_can_be_updated(self) -> None:
        self.assertTrue(self.profile["user_id"])
        self.assertEqual(self.profile["display_name"], "\u06a9\u0627\u0631\u0628\u0631 AtiChat")
        self.database.save_profile(
            self.profile["user_id"], "Ada", "avatar-data", True
        )
        updated = self.database.get_profile()
        self.assertEqual(updated["display_name"], "Ada")
        self.assertEqual(updated["avatar"], "avatar-data")
        self.assertEqual(updated["startup_enabled"], 1)

    def test_legacy_default_profile_name_is_localized(self) -> None:
        import sqlite3

        connection = sqlite3.connect(self.database.path)
        try:
            connection.execute(
                "UPDATE profile SET display_name = 'K1chat User' WHERE singleton = 1"
            )
            connection.commit()
        finally:
            connection.close()
        migrated = Database(self.database.path).get_profile()
        self.assertEqual(migrated["display_name"], "\u06a9\u0627\u0631\u0628\u0631 AtiChat")

    def test_persian_legacy_default_profile_name_uses_current_brand(self) -> None:
        import sqlite3

        connection = sqlite3.connect(self.database.path)
        try:
            connection.execute(
                "UPDATE profile SET display_name = 'کاربر K1chat' WHERE singleton = 1"
            )
            connection.commit()
        finally:
            connection.close()
        migrated = Database(self.database.path).get_profile()
        self.assertEqual(migrated["display_name"], "\u06a9\u0627\u0631\u0628\u0631 AtiChat")

    def test_old_brand_default_profile_name_migrates_to_atichat(self) -> None:
        import sqlite3

        connection = sqlite3.connect(self.database.path)
        try:
            connection.execute(
                "UPDATE profile SET display_name = 'کاربر K1Chat' WHERE singleton = 1"
            )
            connection.commit()
        finally:
            connection.close()
        migrated = Database(self.database.path).get_profile()
        self.assertEqual(
            migrated["display_name"], "\u06a9\u0627\u0631\u0628\u0631 AtiChat"
        )

    def test_private_history_includes_both_directions(self) -> None:
        common = {
            "kind": "private",
            "recipient_name": "",
            "body": "hello",
            "file_path": "",
        }
        self.database.add_message(
            {
                **common,
                "message_id": "outgoing",
                "timestamp": "2026-01-01T10:00:00+03:30",
                "sender_id": "me",
                "sender_name": "Me",
                "recipient_id": "peer",
                "outgoing": True,
            }
        )
        self.database.add_message(
            {
                **common,
                "message_id": "file",
                "kind": "file",
                "timestamp": "2026-01-01T06:31:00+00:00",
                "sender_id": "me",
                "sender_name": "Me",
                "recipient_id": "peer",
                "outgoing": True,
            }
        )
        self.database.add_message(
            {
                **common,
                "message_id": "incoming",
                "timestamp": "2026-01-01T10:02:00+03:30",
                "sender_id": "peer",
                "sender_name": "Peer",
                "recipient_id": "me",
                "outgoing": False,
            }
        )
        history = self.database.get_private_messages("me", "peer")
        self.assertEqual(
            [message["message_id"] for message in history],
            ["outgoing", "file", "incoming"],
        )

    def test_histories_follow_local_insert_order_not_sender_clock(self) -> None:
        for message_id, timestamp in (
            ("older", "2026-01-01T10:00:00+00:00"),
            ("newer-with-slow-clock", "2020-01-01T00:00:00+00:00"),
        ):
            self.database.add_message(
                {
                    "message_id": message_id,
                    "kind": "public",
                    "sender_id": "peer",
                    "sender_name": "Peer",
                    "body": message_id,
                    "timestamp": timestamp,
                    "outgoing": False,
                }
            )
        self.assertEqual(
            [
                message["message_id"]
                for message in self.database.get_public_messages()
            ],
            ["older", "newer-with-slow-clock"],
        )

        for message_id, timestamp in (
            ("private-older", "2026-01-01T10:00:00+00:00"),
            ("private-newer-with-slow-clock", "2020-01-01T00:00:00+00:00"),
        ):
            self.database.add_message(
                {
                    "message_id": message_id,
                    "kind": "private",
                    "sender_id": "peer",
                    "sender_name": "Peer",
                    "recipient_id": "me",
                    "recipient_name": "Me",
                    "body": message_id,
                    "timestamp": timestamp,
                    "outgoing": False,
                }
            )
        self.assertEqual(
            [
                message["message_id"]
                for message in self.database.get_private_messages("me", "peer")
            ],
            ["private-older", "private-newer-with-slow-clock"],
        )

    def test_private_contacts_include_latest_names_for_offline_history(self) -> None:
        messages = [
            {
                "message_id": "first",
                "kind": "private",
                "sender_id": "me",
                "sender_name": "Me",
                "recipient_id": "peer-a",
                "recipient_name": "Old name",
                "body": "hello",
                "timestamp": "2026-01-01T10:00:00+00:00",
                "outgoing": True,
            },
            {
                "message_id": "file",
                "kind": "file",
                "sender_id": "peer-b",
                "sender_name": "Peer B",
                "recipient_id": "me",
                "recipient_name": "Me",
                "body": "document.pdf",
                "timestamp": "2026-01-01T10:01:00+00:00",
                "outgoing": False,
            },
            {
                "message_id": "latest",
                "kind": "private",
                "sender_id": "me",
                "sender_name": "Me",
                "recipient_id": "peer-a",
                "recipient_name": "Current name",
                "body": "welcome back",
                "timestamp": "2026-01-01T10:02:00+00:00",
                "outgoing": True,
            },
        ]
        for message in messages:
            self.database.add_message(message)

        self.assertEqual(
            self.database.get_private_contacts("me"),
            [
                {"user_id": "peer-a", "username": "Current name"},
                {"user_id": "peer-b", "username": "Peer B"},
            ],
        )

    def test_duplicate_message_ids_are_ignored(self) -> None:
        message = {
            "message_id": "duplicate",
            "kind": "public",
            "sender_id": "peer",
            "sender_name": "Peer",
            "body": "hello",
            "timestamp": "2026-01-01T10:00:00+00:00",
            "outgoing": False,
        }
        self.database.add_message(message)
        self.database.add_message(message)
        self.assertEqual(len(self.database.get_public_messages()), 1)

    def test_delete_single_message_and_conversation_history(self) -> None:
        messages = [
            {
                "message_id": "public",
                "kind": "public",
                "sender_id": "peer",
                "sender_name": "Peer",
                "body": "public",
                "timestamp": "2026-01-01T10:00:00+00:00",
                "outgoing": False,
            },
            {
                "message_id": "private",
                "kind": "private",
                "sender_id": "me",
                "sender_name": "Me",
                "recipient_id": "peer",
                "body": "private",
                "timestamp": "2026-01-01T10:01:00+00:00",
                "outgoing": True,
            },
            {
                "message_id": "other-peer",
                "kind": "private",
                "sender_id": "me",
                "sender_name": "Me",
                "recipient_id": "other",
                "body": "other conversation",
                "timestamp": "2026-01-01T10:02:00+00:00",
                "outgoing": True,
            },
        ]
        for message in messages:
            self.database.add_message(message)

        self.assertTrue(self.database.delete_message("public"))
        self.assertFalse(self.database.delete_message("missing"))
        self.assertEqual(self.database.delete_private_conversation("me", "peer"), 1)
        self.assertEqual(len(self.database.get_public_messages()), 0)
        self.assertEqual(
            [message["message_id"] for message in self.database.get_private_messages("me", "other")],
            ["other-peer"],
        )

    def test_delete_all_messages_preserves_profile(self) -> None:
        self.database.add_message(
            {
                "message_id": "message",
                "kind": "public",
                "sender_id": "peer",
                "sender_name": "Peer",
                "body": "hello",
                "timestamp": "2026-01-01T10:00:00+00:00",
                "outgoing": False,
            }
        )
        self.assertEqual(self.database.delete_all_messages(), 1)
        self.assertEqual(self.database.get_public_messages(), [])
        self.assertEqual(self.database.get_profile()["user_id"], self.profile["user_id"])


if __name__ == "__main__":
    unittest.main()
