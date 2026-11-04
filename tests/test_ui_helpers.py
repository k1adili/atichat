import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QIODevice, QPoint, Qt
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QLabel,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QWidget,
)

from ui import (
    ChatWindow,
    FileSendDialog,
    MessageHistory,
    NetworkBridge,
    SettingsDialog,
    _set_windows_startup,
    _format_message_timestamp,
    _gregorian_to_jalali,
    _text_direction,
)


class UiHelperTests(unittest.TestCase):
    def test_gregorian_to_jalali_known_date(self) -> None:
        self.assertEqual(_gregorian_to_jalali(2026, 11, 4), (1405, 8, 13))

    def test_gregorian_to_jalali_year_boundary(self) -> None:
        self.assertEqual(_gregorian_to_jalali(2026, 3, 21), (1405, 1, 1))

    def test_formatted_timestamp_includes_persian_date_and_time(self) -> None:
        formatted = _format_message_timestamp("2026-11-04T10:27:00+00:00")
        self.assertIn("/", formatted)
        self.assertIn(":", formatted)
        self.assertTrue(all(character not in "0123456789" for character in formatted))

    def test_text_direction_follows_first_strong_character(self) -> None:
        self.assertEqual(_text_direction("  123 سلام"), "rtl")
        self.assertEqual(_text_direction("🙂 Hello سلام"), "ltr")
        self.assertEqual(_text_direction("فارسی English"), "rtl")
        self.assertEqual(_text_direction("123 !!!"), "rtl")


class UiWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_new_message_scrolls_history_to_bottom(self) -> None:
        history = MessageHistory()
        history.resize(420, 240)
        history.show()
        for index in range(30):
            history.append_message("کاربر", "۱۴۰۵/۰۸/۱۳ ۱۲:۰۰", str(index), False)
        self.app.processEvents()

        scrollbar = history.scroll_area.verticalScrollBar()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

        scrollbar.setValue(scrollbar.minimum())
        history.append_message("کاربر", "۱۴۰۵/۰۸/۱۳ ۱۲:۰۱", "پیام جدید", False)
        self.app.processEvents()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())
        history.close()

    def test_message_body_alignment_matches_text_direction(self) -> None:
        history = MessageHistory()
        history.append_message(
            "User",
            "۱۴۰۵/۰۸/۱۳ ۱۲:۰۰",
            "سلام",
            False,
            text_direction="rtl",
        )
        persian_body = history.findChild(QLabel, "messageBody")
        self.assertEqual(persian_body.alignment(), Qt.AlignmentFlag.AlignRight)
        history.close()
        english_history = MessageHistory()
        english_history.append_message(
            "User",
            "۱۴۰۵/۰۸/۱۳ ۱۲:۰۰",
            "Hello",
            False,
            text_direction="ltr",
        )
        english_body = english_history.findChild(QLabel, "messageBody")
        self.assertEqual(english_body.alignment(), Qt.AlignmentFlag.AlignLeft)
        english_history.close()

    def test_chat_header_avatar_and_unread_badges(self) -> None:
        profile = {
            "user_id": "local-user",
            "display_name": "کاربر",
            "avatar": "",
            "startup_enabled": False,
        }
        database = Mock()
        database.get_profile.return_value = profile
        database.get_public_messages.return_value = []
        database.get_private_messages.return_value = []
        network = Mock()
        network.peers.return_value = []

        image = QImage(36, 16, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.green)
        buffer = QBuffer()
        self.assertTrue(buffer.open(QIODevice.OpenModeFlag.WriteOnly))
        self.assertTrue(image.save(buffer, "PNG"))
        avatar = base64.b64encode(bytes(buffer.data())).decode("ascii")

        window = ChatWindow(database, network, NetworkBridge(), Path("."))
        peer = {"user_id": "peer-id", "username": "همتا", "avatar": avatar}
        window._add_peer(peer)
        peer_item = window._find_item("peer-id")
        self.assertIsNotNone(peer_item)
        self.assertFalse(peer_item.icon().isNull())
        peer_icon = peer_item.icon().pixmap(40, 40).toImage()
        self.assertEqual(peer_icon.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(peer_icon.pixelColor(20, 20), Qt.GlobalColor.green)
        fallback_avatar = window._make_avatar_pixmap("", "Alice", 40).toImage()
        self.assertEqual(fallback_avatar.pixelColor(0, 0).alpha(), 0)
        self.assertGreater(
            len(
                {
                    fallback_avatar.pixelColor(x, y).rgba()
                    for x in range(40)
                    for y in range(40)
                }
            ),
            2,
        )
        window.show()
        self.app.processEvents()
        chat_header = window.findChild(QWidget, "chatHeader")
        self.assertIsNotNone(chat_header)
        header_background = chat_header.grab().toImage().pixelColor(2, 2)
        history_background = (
            window.messages.scroll_area.grab().toImage().pixelColor(2, 2)
        )
        self.assertEqual(header_background, history_background)
        window.hide()

        with patch.object(window, "_notify"):
            window._handle_event(
                {
                    "type": "public_message",
                    "message_id": "public-message",
                    "user_id": "other-user",
                    "username": "کاربر دیگر",
                    "body": "پیام عمومی",
                    "timestamp": "2026-11-04T08:30:00+00:00",
                }
            )
            window._handle_event(
                {
                    "type": "direct_message",
                    "message_id": "private-message",
                    "user_id": "peer-id",
                    "username": "همتا",
                    "body": "پیام خصوصی",
                    "timestamp": "2026-11-04T08:31:00+00:00",
                }
            )

        public_item = window.user_list.item(0)
        self.assertIn("(۱)", public_item.text())
        self.assertIn("(۱)", peer_item.text())
        window.user_list.setCurrentItem(peer_item)
        self.assertEqual(window.chat_title.text(), "همتا")
        self.assertEqual(window.chat_avatar.pixmap().size().width(), 56)
        self.assertEqual(
            window.chat_avatar.pixmap().toImage().pixelColor(0, 0).alpha(),
            0,
        )
        self.assertEqual(peer_item.text(), "همتا")
        self.assertEqual(window._unread_counts.get("peer-id"), None)
        self.assertEqual(window._unread_counts.get("public"), 1)

        window.user_list.setCurrentItem(public_item)
        self.assertEqual(public_item.text(), "گفت‌وگوی عمومی")
        self.assertNotIn("public", window._unread_counts)
        window._allow_close = True
        window.close()

    def test_message_context_menu_requests_single_message_deletion(self) -> None:
        history = MessageHistory()
        deleted: list[str] = []
        history.deleteMessageRequested.connect(deleted.append)
        row = QWidget()
        with patch.object(QMenu, "exec", new=lambda menu, *_args: menu.actions()[0]):
            history._show_message_menu(row, QPoint(), "message-id")
        self.assertEqual(deleted, ["message-id"])
        history.close()

    def test_file_history_has_reveal_button_and_selects_file_in_explorer(self) -> None:
        profile = {
            "user_id": "local-user",
            "display_name": "کاربر",
            "avatar": "",
            "startup_enabled": False,
        }
        database = Mock()
        database.get_profile.return_value = profile
        database.get_public_messages.return_value = []
        network = Mock()
        network.peers.return_value = []
        window = ChatWindow(database, network, NetworkBridge(), Path("."))

        with tempfile.TemporaryDirectory() as temporary:
            file_path = Path(temporary) / "received.txt"
            file_path.write_text("file", encoding="utf-8")
            revealed: list[str] = []
            window.messages.revealFileRequested.connect(revealed.append)
            window._append_message(
                {
                    "message_id": "received-file",
                    "kind": "file",
                    "sender_name": "همتا",
                    "body": file_path.name,
                    "file_path": str(file_path),
                    "timestamp": "2026-11-04T08:30:00+00:00",
                    "outgoing": False,
                }
            )
            reveal_button = window.messages.findChild(
                QPushButton, "revealFileButton"
            )
            self.assertIsNotNone(reveal_button)
            with (
                patch("ui.sys.platform", "win32"),
                patch("ui.subprocess.Popen") as popen,
            ):
                reveal_button.click()
                self.assertEqual(revealed, [str(file_path)])
            popen.assert_called_once_with(
                ["explorer.exe", "/select,", str(file_path.resolve())]
            )
        window._allow_close = True
        window.close()

    def test_avatar_picker_loads_selected_image_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "avatar.png"
            image = QImage(16, 16, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.green)
            self.assertTrue(image.save(str(image_path)))
            dialog = SettingsDialog(
                {"avatar": "", "display_name": "کاربر", "startup_enabled": False}
            )
            self.assertIn(
                "طرح و توسعه کیوان عدیلی",
                [label.text() for label in dialog.findChildren(QLabel)],
            )
            with patch(
                "ui.QFileDialog.getOpenFileName", return_value=(str(image_path), "")
            ):
                dialog._choose_avatar()
            self.assertTrue(dialog.avatar)
            dialog.close()

    def test_file_send_dialog_supports_caption_and_cancel(self) -> None:
        dialog = FileSendDialog(Path("sample.txt"))
        dialog.caption_input.setPlainText("متن همراه فایل")
        self.assertEqual(dialog.caption_input.toPlainText(), "متن همراه فایل")
        dialog.cancel_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        dialog.close()

    def test_confirmed_file_sends_caption_before_file_and_cancel_sends_nothing(self) -> None:
        profile = {
            "user_id": "local-user",
            "display_name": "کاربر",
            "avatar": "",
            "startup_enabled": False,
        }
        database = Mock()
        database.get_profile.return_value = profile
        database.get_public_messages.return_value = []
        network = Mock()
        network.peers.return_value = []
        network.send_file.return_value = {
            "message_id": "file-message",
            "timestamp": "2026-11-04T08:30:00+00:00",
        }
        window = ChatWindow(database, network, NetworkBridge(), Path("."))
        window._active_peer_id = "peer-id"
        window._online["peer-id"] = {"user_id": "peer-id", "username": "همتا"}

        with tempfile.TemporaryDirectory() as temporary:
            file_path = Path(temporary) / "sample.txt"
            file_path.write_text("file data", encoding="utf-8")
            cancelled_dialog = Mock()
            cancelled_dialog.exec.return_value = QDialog.DialogCode.Rejected
            with patch("ui.FileSendDialog", return_value=cancelled_dialog):
                window._confirm_file_send(file_path)
            network.send_direct.assert_not_called()
            network.send_file.assert_not_called()

            accepted_dialog = Mock()
            accepted_dialog.exec.return_value = QDialog.DialogCode.Accepted
            accepted_dialog.caption_input.toPlainText.return_value = "متن فایل"
            with patch("ui.FileSendDialog", return_value=accepted_dialog):
                window._confirm_file_send(file_path)

        self.assertEqual(
            network.method_calls[-2][0],
            "send_direct",
        )
        self.assertEqual(
            network.method_calls[-1][0],
            "send_file",
        )
        self.assertEqual(network.method_calls[-2][1][1], "متن فایل")
        self.assertEqual(database.add_message.call_count, 2)
        self.assertEqual(database.add_message.call_args_list[0].args[0]["body"], "متن فایل")
        self.assertEqual(database.add_message.call_args_list[1].args[0]["kind"], "file")
        window._allow_close = True
        window.close()

    def test_incoming_notification_uses_system_tray_popup(self) -> None:
        profile = {
            "user_id": "local-user",
            "display_name": "کاربر",
            "avatar": "",
            "startup_enabled": False,
        }
        database = Mock()
        database.get_profile.return_value = profile
        database.get_public_messages.return_value = []
        database.get_private_messages.return_value = []
        network = Mock()
        network.peers.return_value = []

        with (
            patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True),
            patch.object(QSystemTrayIcon, "setVisible"),
            patch.object(QSystemTrayIcon, "showMessage") as show_message,
            patch.object(QApplication, "windowIcon", return_value=QIcon(QPixmap(24, 24))),
        ):
            window = ChatWindow(database, network, NetworkBridge(), Path("."))
            self.assertEqual(window.send_button.text(), "ارسال\nCtrl+Enter")
            self.assertEqual(
                window.messages.scroll_area.objectName(),
                "messageHistoryScrollArea",
            )
            chat_panel = window.findChild(QWidget, "chatPanel")
            self.assertIsNotNone(chat_panel)
            self.assertIsInstance(chat_panel, QFrame)
            self.assertIn(
                "QFrame#chatPanel",
                window.styleSheet(),
            )
            history_actions = window.history_menu.actions()
            self.assertEqual(
                [action.text() for action in history_actions],
                ["پاک‌کردن این گفت‌وگو", "پاک‌کردن همهٔ پیام‌ها"],
            )
            with patch.object(window, "_confirm_history_delete") as confirm_delete:
                history_actions[0].trigger()
                history_actions[1].trigger()
            self.assertEqual(
                confirm_delete.call_args_list,
                [
                    call(all_messages=False),
                    call(all_messages=True),
                ],
            )
            sidebar = window.findChild(QWidget, "userSidebar")
            self.assertIsNotNone(sidebar)
            self.assertIs(window.history_button.parentWidget(), sidebar)
            self.assertNotIn(window.history_button, window.findChild(QWidget, "chatPanel").findChildren(QWidget))
            window.show()
            self.app.processEvents()
            self.assertEqual(window.windowTitle(), "AtiChat")
            self.assertEqual(window.tray_icon.toolTip(), "AtiChat")
            panel_image = chat_panel.grab().toImage()
            self.assertEqual(
                panel_image.pixelColor(panel_image.width() // 2, 0).name(),
                "#343844",
            )
            self.assertEqual(
                window.tray_icon.icon().cacheKey(),
                window.windowIcon().cacheKey(),
            )
            window._notify("فرستنده", "پیام تازه", "remote-user")
            show_message.assert_called_once_with(
                "AtiChat — فرستنده",
                "پیام تازه",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
            window.tray_icon.messageClicked.emit()
            self.assertEqual(window._active_peer_id, "remote-user")
            self.assertEqual(
                window.user_list.currentItem().data(Qt.ItemDataRole.UserRole),
                "remote-user",
            )
            window._notify("فرستنده", "پیام عمومی", None)
            window.tray_icon.messageClicked.emit()
            self.assertIsNone(window._active_peer_id)
            self.assertEqual(
                window.user_list.currentItem().data(Qt.ItemDataRole.UserRole),
                "public",
            )
            network.stop.reset_mock()
            with patch.object(window.tray_icon, "isVisible", return_value=True):
                window.show()
                window.close()
                self.assertFalse(window.isVisible())
                network.stop.assert_not_called()
                with patch.object(QApplication, "quit") as quit_application:
                    window._quit_from_tray()
                    quit_application.assert_called_once_with()
                network.stop.assert_called_once()
                window._stop_network()
                network.stop.assert_called_once()

    def test_startup_registration_uses_and_verifies_current_executable_path(self) -> None:
        values: dict[str, tuple[str, int]] = {}

        class FakeKey:
            def __enter__(self) -> "FakeKey":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        def set_value(_key: object, name: str, _reserved: int, value_type: int, value: str) -> None:
            values[name] = (value, value_type)

        def query_value(_key: object, name: str) -> tuple[str, int]:
            if name not in values:
                raise FileNotFoundError(name)
            return values[name]

        def delete_value(_key: object, name: str) -> None:
            if name not in values:
                raise FileNotFoundError(name)
            del values[name]

        winreg = Mock()
        winreg.HKEY_CURRENT_USER = object()
        winreg.KEY_SET_VALUE = 2
        winreg.KEY_QUERY_VALUE = 1
        winreg.REG_SZ = 1
        winreg.CreateKeyEx.return_value = FakeKey()
        winreg.SetValueEx.side_effect = set_value
        winreg.QueryValueEx.side_effect = query_value
        winreg.DeleteValue.side_effect = delete_value

        with (
            patch("ui.sys.platform", "win32"),
            patch("ui.sys.executable", r"C:\Program Files\AtiChat\AtiChat.exe"),
            patch.object(sys, "frozen", True, create=True),
            patch.dict("sys.modules", {"winreg": winreg}),
        ):
            values["K1chat"] = ("old", winreg.REG_SZ)
            values["K1Chat"] = ("old", winreg.REG_SZ)
            _set_windows_startup(True)
            self.assertEqual(
                values["AtiChat"],
                ('"C:\\Program Files\\AtiChat\\AtiChat.exe"', winreg.REG_SZ),
            )
            self.assertNotIn("K1Chat", values)
            self.assertNotIn("K1chat", values)
            _set_windows_startup(False)
            self.assertNotIn("AtiChat", values)


if __name__ == "__main__":
    unittest.main()
