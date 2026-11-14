"""PyQt6 user interface for AtiChat."""

from __future__ import annotations

import base64
import html
import subprocess
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QBuffer, QIODevice, QObject, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent,
    QBrush,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QAction,
    QFont,
    QPainter,
    QPainterPath,
    QIcon,
    QImageReader,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)
from app_version import APP_VERSION
from database import Database
from network import MAX_MESSAGE_LENGTH, NetworkManager


class NetworkBridge(QObject):
    event = pyqtSignal(dict)


class FileDropTextEdit(QTextEdit):
    filesDropped = pyqtSignal(list)
    sendRequested = pyqtSignal()

    def keyPressEvent(self, event: Any) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() and Path(url.toLocalFile()).is_file()
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        file_paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile() and Path(url.toLocalFile()).is_file()
        ]
        if not file_paths:
            super().dropEvent(event)
            return
        self.filesDropped.emit(file_paths)
        event.acceptProposedAction()


class MessageHistory(QWidget):
    linkActivated = pyqtSignal(str)
    deleteMessageRequested = pyqtSignal(str)
    revealFileRequested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("messageHistoryScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content = QWidget()
        self.content.setObjectName("messageHistoryContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(8, 8, 8, 8)
        self.content_layout.setSpacing(6)
        self.content_layout.addStretch(1)
        self.scroll_area.setWidget(self.content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll_area)
        self.scroll_area.verticalScrollBar().rangeChanged.connect(
            self._scroll_to_bottom_on_range_change
        )

    def clear(self) -> None:
        while self.content_layout.count() > 1:
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def append_message(
        self,
        sender: str,
        timestamp: str,
        body: str,
        outgoing: bool,
        message_id: str = "",
        text_direction: str = "rtl",
        file_path: str = "",
    ) -> None:
        alignment = Qt.AlignmentFlag.AlignRight if outgoing else Qt.AlignmentFlag.AlignLeft
        bubble_color = "#174b35" if outgoing else "#26332c"
        border_color = "#168a4a" if outgoing else "#3d5b48"
        text_color = "#e7fff0" if outgoing else "#e2eae4"
        sender_color = "#65e995" if outgoing else "#a5d6b3"

        row = QWidget()
        row.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)

        bubble = QFrame()
        bubble.setObjectName("messageBubble")
        bubble.setStyleSheet(
            f"QFrame#messageBubble {{ background-color: {bubble_color}; "
            f"border: 1px solid {border_color}; border-radius: 12px; }}"
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 8, 12, 8)
        bubble_layout.setSpacing(4)

        header = QWidget()
        header.setStyleSheet("background: transparent; border: none;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        header_layout.setDirection(QHBoxLayout.Direction.RightToLeft)
        sender_label = QLabel(sender)
        sender_label.setStyleSheet(
            f"color: {sender_color}; font-weight: 600; background: transparent; border: none;"
        )
        timestamp_label = QLabel(timestamp)
        timestamp_label.setStyleSheet(
            "color: #c4d2c8; font-size: 9pt; background: transparent; border: none;"
        )
        header_layout.addWidget(sender_label)
        header_layout.addWidget(timestamp_label)
        header_layout.addStretch(1)
        message_body = QLabel(body)
        message_body.setTextFormat(Qt.TextFormat.RichText)
        message_body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        message_body.setWordWrap(True)
        message_body.setObjectName("messageBody")
        message_body.setAlignment(
            Qt.AlignmentFlag.AlignRight
            if text_direction == "rtl"
            else Qt.AlignmentFlag.AlignLeft
        )
        message_body.setStyleSheet(
            f"QLabel {{ color: {text_color}; background: transparent; border: none; }}"
        )
        message_body.linkActivated.connect(self.linkActivated)
        bubble_layout.addWidget(header)
        bubble_layout.addWidget(message_body)
        reveal_button: QPushButton | None = None
        if file_path:
            reveal_button = QPushButton("نمایش در پوشه")
            reveal_button.setObjectName("revealFileButton")
            reveal_button.setToolTip("بازکردن پوشهٔ فایل")
            reveal_button.clicked.connect(
                lambda _checked=False, path=file_path:
                self.revealFileRequested.emit(path)
            )
            bubble_layout.addWidget(reveal_button, 0, Qt.AlignmentFlag.AlignRight)
        bubble.setMaximumWidth(640)
        context_menu_widgets = [row, bubble, header, sender_label, timestamp_label, message_body]
        if reveal_button is not None:
            context_menu_widgets.append(reveal_button)
        for widget in context_menu_widgets:
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda position, target=widget, item_id=message_id:
                self._show_message_menu(target, position, item_id)
            )

        if alignment == Qt.AlignmentFlag.AlignRight:
            row_layout.addStretch(1)
            row_layout.addWidget(bubble, 0, alignment)
        else:
            row_layout.addWidget(bubble, 0, alignment)
            row_layout.addStretch(1)
        self.content_layout.insertWidget(self.content_layout.count() - 1, row)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _show_message_menu(
        self, row: QWidget, position: Any, message_id: str
    ) -> None:
        if not message_id:
            return
        menu = QMenu(self)
        delete_action = menu.addAction("حذف این پیام از تاریخچه")
        if menu.exec(row.mapToGlobal(position)) == delete_action:
            self.deleteMessageRequested.emit(message_id)

    def _scroll_to_bottom_on_range_change(self, _minimum: int, _maximum: int) -> None:
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        scrollbar = self.scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


class FileSendDialog(QDialog):
    def __init__(self, file_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ارسال فایل")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(420)
        self.caption_input = QTextEdit()
        self.caption_input.setObjectName("fileCaptionInput")
        self.caption_input.setPlaceholderText("متن همراه فایل (اختیاری)…")
        self.caption_input.setFixedHeight(100)
        self.send_button = QPushButton("ارسال فایل")
        self.cancel_button = QPushButton("لغو")
        self.send_button.clicked.connect(self._accept_if_valid)
        self.cancel_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.send_button)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"فایل انتخاب‌شده: {file_path.name}"))
        layout.addWidget(self.caption_input)
        layout.addLayout(buttons)

    def _accept_if_valid(self) -> None:
        if len(self.caption_input.toPlainText().strip()) > MAX_MESSAGE_LENGTH:
            _show_message(
                self,
                QMessageBox.Icon.Warning,
                "متن طولانی است",
                f"حداکثر طول متن {_persian_digits(str(MAX_MESSAGE_LENGTH))} نویسه است.",
            )
            return
        self.accept()


def _set_windows_startup(enabled: bool) -> None:
    if sys.platform != "win32":
        raise RuntimeError("تنظیم اجرای خودکار فقط در ویندوز در دسترس است.")

    import winreg

    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "AtiChat"
    legacy_app_names = ("K1Chat", "K1chat")
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        run_key,
        0,
        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
    ) as key:
        for legacy_app_name in legacy_app_names:
            try:
                winreg.DeleteValue(key, legacy_app_name)
            except FileNotFoundError:
                pass
        if enabled:
            if getattr(sys, "frozen", False):
                command = subprocess.list2cmdline([sys.executable])
            else:
                script = Path(__file__).with_name("main.py")
                command = subprocess.list2cmdline([sys.executable, str(script)])
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, command)
            registered_command, registered_type = winreg.QueryValueEx(key, app_name)
            if registered_type != winreg.REG_SZ or registered_command != command:
                raise OSError("ثبت اجرای خودکار AtiChat در رجیستری تأیید نشد.")
        else:
            try:
                winreg.DeleteValue(key, app_name)
            except FileNotFoundError:
                pass
            try:
                winreg.QueryValueEx(key, app_name)
            except FileNotFoundError:
                pass
            else:
                raise OSError("حذف اجرای خودکار AtiChat از رجیستری تأیید نشد.")


def _show_message(
    parent: QWidget,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
) -> None:
    message_box = QMessageBox(icon, title, text, QMessageBox.StandardButton.Ok, parent)
    message_box.button(QMessageBox.StandardButton.Ok).setText("باشه")
    message_box.exec()


def _persian_digits(value: str) -> str:
    return value.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _text_direction(text: str) -> str:
    for character in text:
        bidi_class = unicodedata.bidirectional(character)
        if bidi_class in ("R", "AL"):
            return "rtl"
        if bidi_class == "L":
            return "ltr"
    return "rtl"


def _gregorian_to_jalali(year: int, month: int, day: int) -> tuple[int, int, int]:
    month_days = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    adjusted_year = year + (1 if month > 2 else 0)
    days = (
        355666
        + 365 * year
        + (adjusted_year + 3) // 4
        - (adjusted_year + 99) // 100
        + (adjusted_year + 399) // 400
        + day
        + month_days[month - 1]
    )
    jalali_year = -1595 + 33 * (days // 12053)
    days %= 12053
    jalali_year += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jalali_year += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jalali_month = 1 + days // 31
        jalali_day = 1 + days % 31
    else:
        jalali_month = 7 + (days - 186) // 30
        jalali_day = 1 + (days - 186) % 30
    return jalali_year, jalali_month, jalali_day


def _format_message_timestamp(timestamp: str) -> str:
    try:
        message_time = datetime.fromisoformat(timestamp).astimezone()
        year, month, day = _gregorian_to_jalali(
            message_time.year, message_time.month, message_time.day
        )
        formatted = (
            f"{year:04d}/{month:02d}/{day:02d} "
            f"{message_time.hour:02d}:{message_time.minute:02d}"
        )
    except (ValueError, OverflowError):
        formatted = timestamp[:16]
    return _persian_digits(formatted)


class SettingsDialog(QDialog):
    def __init__(self, profile: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("تنظیمات و نمایه")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(390)
        self.avatar = str(profile["avatar"])
        self.name_input = QLineEdit(str(profile["display_name"]))
        self.name_input.setMaxLength(64)
        self.avatar_preview = QLabel("تصویری انتخاب نشده است")
        self.avatar_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_preview.setMinimumHeight(78)
        self._show_avatar()

        self.startup_checkbox = QCheckBox("اجرای AtiChat هنگام ورود به ویندوز")
        self.startup_checkbox.setChecked(bool(profile["startup_enabled"]))
        self.startup_checkbox.setEnabled(sys.platform == "win32")

        choose_avatar = QPushButton("انتخاب تصویر…")
        choose_avatar.clicked.connect(self._choose_avatar)
        save_button = QPushButton("ذخیره")
        save_button.clicked.connect(self.accept)
        cancel_button = QPushButton("لغو")
        cancel_button.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("نام نمایشی", self.name_input)
        form.addRow("تصویر نمایه", self.avatar_preview)
        form.addRow("", choose_avatar)
        form.addRow("", self.startup_checkbox)
        self.version_label = QLabel(APP_VERSION)
        self.version_label.setObjectName("appVersion")
        form.addRow("نسخهٔ برنامه", self.version_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        developer_credit = QLabel("طرح و توسعه کیوان عدیلی")
        developer_credit.setObjectName("muted")
        developer_credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(developer_credit)

    def _choose_avatar(self) -> None:
        image_path, _ = QFileDialog.getOpenFileName(
            self,
            "انتخاب تصویر نمایه",
            str(Path.home()),
            "تصاویر (*.png *.jpg *.jpeg *.bmp *.webp);;همهٔ فایل‌ها (*)",
        )
        if not image_path:
            return
        image = QImageReader(image_path).read()
        if image.isNull():
            _show_message(self, QMessageBox.Icon.Warning, "تصویر نامعتبر", "خواندن این تصویر ممکن نیست.")
            return
        scaled = image.scaled(
            48,
            48,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        buffer = QBuffer()
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not scaled.save(buffer, "JPEG", 75):
            _show_message(self, QMessageBox.Icon.Warning, "تصویر نامعتبر", "آماده‌سازی این تصویر ممکن نیست.")
            return
        encoded = base64.b64encode(bytes(buffer.data())).decode("ascii")
        if len(encoded) > 16_000:
            _show_message(
                self,
                QMessageBox.Icon.Warning,
                "تصویر بزرگ است",
                "تصویر ساده‌تر یا کم‌حجم‌تری انتخاب کنید.",
            )
            return
        self.avatar = encoded
        self._show_avatar()

    def _show_avatar(self) -> None:
        if not self.avatar:
            self.avatar_preview.setPixmap(QPixmap())
            self.avatar_preview.setText("تصویری انتخاب نشده است")
            return
        try:
            raw = base64.b64decode(self.avatar, validate=True)
            pixmap = QPixmap()
            if pixmap.loadFromData(raw):
                self.avatar_preview.setPixmap(pixmap.scaled(64, 64))
                self.avatar_preview.setText("")
                return
        except (ValueError, base64.binascii.Error):
            pass
        self.avatar_preview.setPixmap(QPixmap())
        self.avatar_preview.setText("تصویر نمایه در دسترس نیست")


class ChatWindow(QMainWindow):
    def __init__(
        self,
        database: Database,
        network: NetworkManager,
        bridge: NetworkBridge,
        downloads_dir: Path,
    ) -> None:
        super().__init__()
        self.database = database
        self.network = network
        self.bridge = bridge
        self.downloads_dir = downloads_dir
        self.profile = database.get_profile()
        self._online: dict[str, dict[str, Any]] = {}
        self._unread_counts: dict[str, int] = {}
        self._active_peer_id: str | None = None
        self._notification_target: tuple[str | None, str] | None = None
        self._allow_close = False
        self._network_stopped = False
        self._last_typing_at = 0.0
        self._typing_timer = QTimer(self)
        self._typing_timer.setSingleShot(True)
        self._typing_timer.timeout.connect(lambda: self.status_label.setText(" "))
        self.setWindowTitle("AtiChat")
        self.resize(980, 680)
        self.setMinimumSize(720, 480)
        self.setAcceptDrops(True)
        app_icon = QApplication.windowIcon()
        self.setWindowIcon(app_icon)
        self.tray_icon = QSystemTrayIcon(app_icon, self)
        self.tray_icon.setToolTip("AtiChat")
        self.tray_icon.setVisible(QSystemTrayIcon.isSystemTrayAvailable())
        self.tray_icon.messageClicked.connect(self._open_notification_target)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_menu = QMenu(self)
        show_action = QAction("نمایش AtiChat", self.tray_menu)
        show_action.triggered.connect(self._show_from_tray)
        self.tray_menu.addAction(show_action)
        self.tray_menu.addSeparator()
        quit_action = QAction("خروج", self.tray_menu)
        quit_action.setObjectName("quitAtiChatAction")
        quit_action.triggered.connect(self._quit_from_tray)
        self.tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.setStyleSheet(
            """
            QWidget { background: #17191f; color: #e8eaf0; font-family: Vazirmatn; font-size: 10pt; }
            QMainWindow, QDialog { background: #17191f; }
            QFrame#chatPanel {
                background: #20232b; border: 1px solid #343844; border-radius: 8px;
            }
            QWidget#chatHeader { background: #20232b; }
            QWidget#chatHeader QLabel { background: transparent; }
            QLabel#chatTitle {
                color: #65e995; font-size: 17pt; font-weight: 700;
            }
            QScrollArea#messageHistoryScrollArea {
                background: #20232b; border: none;
            }
            QWidget#messageHistoryContent, QWidget#messageHistoryContent QWidget {
                background: #20232b;
            }
            QListWidget, QTextEdit, QLineEdit {
                background: #20232b; border: 1px solid #343844; border-radius: 8px;
                padding: 8px; selection-background-color: #087c3d;
            }
            QListWidget::item { padding: 10px 8px; border-radius: 6px; }
            QListWidget::item:selected { background: #174b35; }
            QPushButton { background: #07883f; border: 0; border-radius: 7px; padding: 9px 14px; }
            QPushButton:hover { background: #09a34c; }
            QPushButton:disabled { background: #41434b; color: #a0a2aa; }
            QPushButton#downloads { background: #343a48; }
            QPushButton#downloads:hover { background: #454d5e; }
            QLabel#muted { color: #a6a8b2; }
            QSplitter::handle { background: #30333b; }
            """
        )

        self.user_list = QListWidget()
        public_item = QListWidgetItem("گفت‌وگوی عمومی")
        public_item.setData(Qt.ItemDataRole.UserRole, "public")
        public_item.setData(Qt.ItemDataRole.UserRole + 1, "گفت‌وگوی عمومی")
        self.user_list.addItem(public_item)
        self._update_unread_item(public_item, "public")
        self.user_list.setCurrentItem(public_item)
        self.user_list.currentItemChanged.connect(self._selection_changed)

        chat_header = QWidget()
        chat_header.setObjectName("chatHeader")
        chat_header.setMinimumHeight(72)
        title_row = QHBoxLayout(chat_header)
        title_row.setContentsMargins(10, 6, 10, 6)
        self.chat_title = QLabel("گفت‌وگوی عمومی")
        self.chat_title.setObjectName("chatTitle")
        self.chat_title.setStyleSheet(
            "color: #65e995; font-size: 17pt; font-weight: 700; "
            "background: transparent;"
        )
        self.chat_avatar = QLabel()
        self.chat_avatar.setObjectName("chatAvatar")
        self.chat_avatar.setFixedSize(56, 56)
        self.chat_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label = QLabel(" ")
        self.status_label.setObjectName("muted")
        self.history_button = QPushButton("مدیریت تاریخچه")
        self.history_button.setObjectName("historyActions")
        self.history_menu = QMenu(self.history_button)
        clear_conversation_action = QAction("پاک‌کردن این گفت‌وگو", self.history_menu)
        clear_conversation_action.triggered.connect(
            lambda: self._confirm_history_delete(all_messages=False)
        )
        clear_all_action = QAction("پاک‌کردن همهٔ پیام‌ها", self.history_menu)
        clear_all_action.triggered.connect(
            lambda: self._confirm_history_delete(all_messages=True)
        )
        self.history_menu.addAction(clear_conversation_action)
        self.history_menu.addAction(clear_all_action)
        self.history_button.setMenu(self.history_menu)
        settings_button = QPushButton("نمایه و تنظیمات")
        settings_button.clicked.connect(self._open_settings)
        title_row.addWidget(self.chat_avatar)
        title_row.addWidget(self.chat_title)
        title_row.addWidget(self.status_label, 1)
        title_row.addWidget(settings_button)

        self.messages = MessageHistory()
        self.composer = FileDropTextEdit()
        self.composer.setPlaceholderText(
            "پیامتان را بنویسید یا فایل را اینجا رها کنید…"
        )
        self.composer.setFixedHeight(86)
        self.composer.textChanged.connect(self._on_composer_changed)
        self.composer.filesDropped.connect(self._send_dropped_files)
        self.composer.sendRequested.connect(self._send_message)
        self.send_button = QPushButton("ارسال\nCtrl+Enter")
        self.send_button.setMinimumSize(94, 60)
        self.send_button.setToolTip("ارسال پیام (Ctrl+Enter)")
        self.send_button.clicked.connect(self._send_message)
        self.file_button = QPushButton("ارسال فایل…")
        self.file_button.clicked.connect(self._send_file)
        self.file_button.setEnabled(False)
        self.downloads_button = QPushButton("پوشهٔ دریافت‌ها")
        self.downloads_button.setObjectName("downloads")
        self.downloads_button.clicked.connect(self._open_downloads)
        compose_row = QHBoxLayout()
        compose_row.addWidget(self.file_button)
        compose_row.addWidget(self.downloads_button)
        compose_row.addWidget(self.composer, 1)
        compose_row.addWidget(self.send_button)

        chat_panel = QFrame()
        chat_panel.setObjectName("chatPanel")
        chat_panel.setFrameShape(QFrame.Shape.NoFrame)
        chat_panel.setStyleSheet(
            "QFrame#chatPanel { background: #20232b; border: 1px solid #343844; "
            "border-radius: 8px; }"
        )
        chat_layout = QVBoxLayout(chat_panel)
        chat_layout.addWidget(chat_header)
        chat_layout.addWidget(self.messages, 1)
        chat_layout.addLayout(compose_row)

        sidebar = QWidget()
        sidebar.setObjectName("userSidebar")
        sidebar.setMinimumWidth(190)
        sidebar_layout = QVBoxLayout(sidebar)
        self.profile_label = QLabel(f"AtiChat · {self.profile['display_name']}")
        sidebar_layout.addWidget(self.profile_label)
        sidebar_layout.addWidget(self.user_list, 1)
        self.connection_label = QLabel("در حال یافتن کاربران…")
        self.connection_label.setObjectName("muted")
        sidebar_layout.addWidget(self.connection_label)
        self.refresh_users_button = QPushButton("به‌روزرسانی کاربران")
        self.refresh_users_button.setObjectName("refreshUsersButton")
        self.refresh_users_button.setToolTip(
            "اعلان حضور را همین حالا بفرست و کاربران شبکه را دوباره بررسی کن"
        )
        self.refresh_users_button.clicked.connect(self._refresh_users)
        sidebar_layout.addWidget(self.refresh_users_button)
        sidebar_layout.addWidget(self.history_button)

        splitter = QSplitter()
        splitter.addWidget(sidebar)
        splitter.addWidget(chat_panel)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        self.messages.linkActivated.connect(
            lambda link: self._open_message_link(QUrl(link))
        )
        self.messages.revealFileRequested.connect(self._reveal_file)
        self.bridge.event.connect(self._handle_event)
        self.messages.deleteMessageRequested.connect(self._delete_message)

        self._update_chat_header("گفت‌وگوی عمومی", "")
        for peer in self.network.peers():
            self._add_peer(peer)
        for contact in self.database.get_private_contacts(self.profile["user_id"]):
            self._add_offline_contact(contact)
        self._load_history()

    def _add_offline_contact(self, contact: dict[str, Any]) -> None:
        peer_id = str(contact["user_id"])
        if self._find_item(peer_id) is not None:
            return
        label = str(contact.get("username") or peer_id)
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, peer_id)
        item.setData(Qt.ItemDataRole.UserRole + 1, label)
        item.setIcon(self._avatar_icon("", label))
        self.user_list.addItem(item)
        self._update_unread_item(item, peer_id)

    def _add_peer(self, peer: dict[str, Any]) -> None:
        peer_id = peer["user_id"]
        self._online[peer_id] = peer
        item = self._find_item(peer_id)
        label = str(peer["username"])
        icon = self._avatar_icon(
            str(peer.get("avatar", "")), str(peer.get("username", ""))
        )
        if item is None:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, peer_id)
            self.user_list.addItem(item)
        item.setData(Qt.ItemDataRole.UserRole + 1, label)
        item.setIcon(icon)
        self._update_unread_item(item, peer_id)
        self.connection_label.setText(f"{_persian_digits(str(len(self._online)))} کاربر برخط")
        if self._active_peer_id == peer_id:
            self._update_chat_header(peer["username"], str(peer.get("avatar", "")))
            self.file_button.setEnabled(True)

    @classmethod
    def _avatar_icon(cls, encoded: str, display_name: str) -> QIcon:
        return QIcon(cls._make_avatar_pixmap(encoded, display_name, 40))

    @staticmethod
    def _make_avatar_pixmap(encoded: str, display_name: str, size: int) -> QPixmap:
        source = ChatWindow._avatar_pixmap(encoded)
        avatar = QPixmap(size, size)
        avatar.fill(Qt.GlobalColor.transparent)
        painter = QPainter(avatar)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        circle = QPainterPath()
        circle.addEllipse(0, 0, size, size)
        painter.setClipPath(circle)
        if not source.isNull():
            scaled = source.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            crop_left = (scaled.width() - size) // 2
            crop_top = (scaled.height() - size) // 2
            painter.drawPixmap(
                0,
                0,
                scaled,
                crop_left,
                crop_top,
                size,
                size,
            )
        else:
            painter.fillPath(circle, QColor("#087c3d"))
            initial = display_name.strip()[:1] or "?"
            painter.setPen(Qt.GlobalColor.white)
            painter.setFont(QFont("Vazirmatn", max(10, size // 2), QFont.Weight.Bold))
            painter.drawText(
                avatar.rect(),
                Qt.AlignmentFlag.AlignCenter,
                initial,
            )
        painter.end()
        return avatar

    @staticmethod
    def _avatar_pixmap(encoded: str) -> QPixmap:
        if not encoded:
            return QPixmap()
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error):
            return QPixmap()
        pixmap = QPixmap()
        if not pixmap.loadFromData(raw):
            return QPixmap()
        return pixmap

    def _remove_peer(self, peer_id: str) -> None:
        self._online.pop(peer_id, None)
        item = self._find_item(peer_id)
        if item is not None:
            self._update_unread_item(item, peer_id)
        self.connection_label.setText(
            f"{_persian_digits(str(len(self._online)))} کاربر برخط"
            if self._online
            else "کاربر دیگری برخط نیست"
        )
        if self._active_peer_id == peer_id:
            self.file_button.setEnabled(False)
            self.status_label.setText("کاربر آفلاین است؛ تاریخچهٔ گفت‌وگو در دسترس است.")

    def _find_item(self, peer_id: str) -> QListWidgetItem | None:
        for index in range(self.user_list.count()):
            item = self.user_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == peer_id:
                return item
        return None

    def _selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        selected = current.data(Qt.ItemDataRole.UserRole)
        self._active_peer_id = None if selected == "public" else str(selected)
        self._clear_unread(self._active_peer_id)
        if self._active_peer_id is None:
            self._update_chat_header("گفت‌وگوی عمومی", "")
            self.file_button.setEnabled(False)
        else:
            peer = self._online.get(self._active_peer_id)
            self._update_chat_header(
                peer["username"] if peer else str(current.data(Qt.ItemDataRole.UserRole + 1)),
                str(peer.get("avatar", "")) if peer else "",
            )
            self.file_button.setEnabled(peer is not None)
        self.status_label.setText(" ")
        self._load_history()

    def _update_chat_header(self, title: str, avatar: str) -> None:
        self.chat_title.setText(title)
        self.chat_avatar.setPixmap(
            self._make_avatar_pixmap(avatar, title, 56)
        )

    def _update_unread_item(self, item: QListWidgetItem, peer_id: str) -> None:
        count = self._unread_counts.get(peer_id, 0)
        base_label = str(item.data(Qt.ItemDataRole.UserRole + 1))
        presence = (
            "" if peer_id == "public" or peer_id in self._online else " (آفلاین)"
        )
        suffix = f"  ({_persian_digits(str(count))})" if count else ""
        item.setText(f"{base_label}{presence}{suffix}")
        item.setForeground(QBrush(QColor("#65e995" if count else "#e8eaf0")))
        font = item.font()
        font.setBold(bool(count))
        item.setFont(font)
        item.setToolTip(f"{base_label}: {_persian_digits(str(count))} پیام خوانده‌نشده")

    def _mark_unread(self, peer_id: str | None) -> None:
        if peer_id == self._active_peer_id and self.isActiveWindow():
            return
        unread_key = peer_id or "public"
        self._unread_counts[unread_key] = self._unread_counts.get(unread_key, 0) + 1
        if peer_id is None:
            item = self.user_list.item(0)
        else:
            item = self._find_item(peer_id)
        if item is not None:
            self._update_unread_item(item, unread_key)

    def _clear_unread(self, peer_id: str | None) -> None:
        unread_key = peer_id or "public"
        self._unread_counts.pop(unread_key, None)
        item = self.user_list.item(0) if peer_id is None else self._find_item(peer_id)
        if item is not None:
            self._update_unread_item(item, unread_key)

    def _load_history(self) -> None:
        if self._active_peer_id is None:
            history = self.database.get_public_messages()
        else:
            history = self.database.get_private_messages(
                self.profile["user_id"], self._active_peer_id
            )
        self.messages.clear()
        for message in history:
            self._append_message(message)

    def _append_message(self, message: dict[str, Any]) -> None:
        outgoing = bool(message["outgoing"])
        sender = "شما" if outgoing else message["sender_name"]
        displayed_time = _format_message_timestamp(str(message["timestamp"]))
        body = message.get("body", "")
        file_path = ""
        if message["kind"] == "file":
            filename = str(body or Path(message.get("file_path", "")).name)
            file_path = str(message.get("file_path", ""))
            if file_path:
                body = (
                    f'<a href="{html.escape(Path(file_path).resolve().as_uri(), quote=True)}" '
                    'style="color:#8fddc1;text-decoration:underline">'
                    f"{html.escape(filename)}</a>"
                )
            else:
                body = html.escape(filename)
            body = f"پرونده: {body}"
        else:
            body = html.escape(str(body)).replace("\n", "<br>")
        direction = _text_direction(
            str(message.get("body") or message.get("file_path", ""))
        )
        text_align = "right" if direction == "rtl" else "left"
        self.messages.append_message(
            sender=str(sender),
            timestamp=displayed_time,
            body=f'<div dir="{direction}" style="text-align:{text_align}">{body}</div>',
            outgoing=outgoing,
            message_id=str(message["message_id"]),
            text_direction=direction,
            file_path=file_path,
        )

    def _delete_message(self, message_id: str) -> None:
        if self.database.delete_message(message_id):
            self._load_history()

    def _confirm_history_delete(self, all_messages: bool) -> None:
        scope = (
            "تمام پیام‌های همهٔ گفت‌وگوها"
            if all_messages
            else "تمام پیام‌های این گفت‌وگو"
        )
        message_box = QMessageBox(
            QMessageBox.Icon.Warning,
            "تأیید حذف",
            f"{scope} فقط از تاریخچهٔ این دستگاه حذف می‌شوند. فایل‌های دریافت‌شده "
            "پاک نمی‌شوند.\nادامه می‌دهید؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self,
        )
        message_box.button(QMessageBox.StandardButton.Yes).setText("حذف")
        message_box.button(QMessageBox.StandardButton.No).setText("لغو")
        message_box.setDefaultButton(QMessageBox.StandardButton.No)
        if message_box.exec() != QMessageBox.StandardButton.Yes:
            return
        if all_messages:
            self.database.delete_all_messages()
        elif self._active_peer_id is None:
            self.database.delete_public_messages()
        else:
            self.database.delete_private_conversation(
                self.profile["user_id"], self._active_peer_id
            )
        self._load_history()

    def _send_message(self) -> None:
        body = self.composer.toPlainText().strip()
        if not body:
            return
        if len(body) > MAX_MESSAGE_LENGTH:
            _show_message(
                self,
                QMessageBox.Icon.Warning,
                "پیام طولانی است",
                f"حداکثر طول پیام {_persian_digits(str(MAX_MESSAGE_LENGTH))} نویسه است.",
            )
            return
        timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        outgoing = {
            "message_id": str(uuid.uuid4()),
            "kind": "public" if self._active_peer_id is None else "private",
            "sender_id": self.profile["user_id"],
            "sender_name": self.profile["display_name"],
            "recipient_id": self._active_peer_id or "",
            "recipient_name": "",
            "body": body,
            "file_path": "",
            "timestamp": timestamp,
            "outgoing": True,
        }
        try:
            if self._active_peer_id is None:
                self.network.send_public(body, outgoing["message_id"], timestamp)
            else:
                peer = self._online.get(self._active_peer_id)
                if peer is None:
                    raise ValueError("این کاربر دیگر برخط نیست.")
                outgoing["recipient_name"] = peer["username"]
                self.network.send_direct(self._active_peer_id, body, outgoing["message_id"], timestamp)
            self.database.add_message(outgoing)
            self._append_message(outgoing)
            self.composer.clear()
        except (OSError, ValueError, RuntimeError) as error:
            _show_message(self, QMessageBox.Icon.Warning, "پیام ارسال نشد", str(error))

    def _send_file(self) -> None:
        if self._active_peer_id is None:
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "انتخاب فایل برای ارسال",
            str(Path.home()),
            "همهٔ فایل‌ها (*.*)",
        )
        if not file_path:
            return
        self._confirm_file_send(Path(file_path))

    def _confirm_file_send(self, file_path: Path) -> None:
        if self._active_peer_id is None:
            _show_message(
                self,
                QMessageBox.Icon.Information,
                "گفت‌وگوی خصوصی لازم است",
                "برای ارسال فایل، ابتدا گفت‌وگوی خصوصی با یک کاربر را انتخاب کنید.",
            )
            return
        if not file_path.is_file():
            _show_message(self, QMessageBox.Icon.Warning, "فایل پیدا نشد", "فایل انتخاب‌شده در دسترس نیست.")
            return
        dialog = FileSendDialog(file_path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._send_file_path(file_path, dialog.caption_input.toPlainText().strip())

    def _send_file_path(self, file_path: Path, caption: str = "") -> None:
        if self._active_peer_id is None:
            return
        try:
            peer = self._online.get(self._active_peer_id)
            if peer is None:
                raise ValueError("این کاربر دیگر برخط نیست.")
            if caption:
                timestamp = datetime.now(timezone.utc).isoformat(
                    timespec="microseconds"
                )
                caption_message = {
                    "message_id": str(uuid.uuid4()),
                    "kind": "private",
                    "sender_id": self.profile["user_id"],
                    "sender_name": self.profile["display_name"],
                    "recipient_id": self._active_peer_id,
                    "recipient_name": peer["username"],
                    "body": caption,
                    "file_path": "",
                    "timestamp": timestamp,
                    "outgoing": True,
                }
                self.network.send_direct(
                    self._active_peer_id,
                    caption,
                    caption_message["message_id"],
                    timestamp,
                )
                self.database.add_message(caption_message)
                self._append_message(caption_message)
            transfer = self.network.send_file(self._active_peer_id, str(file_path))
            message = {
                "message_id": transfer["message_id"],
                "kind": "file",
                "sender_id": self.profile["user_id"],
                "sender_name": self.profile["display_name"],
                "recipient_id": self._active_peer_id,
                "recipient_name": peer["username"],
                "body": file_path.name,
                "file_path": str(file_path),
                "timestamp": transfer["timestamp"],
                "outgoing": True,
            }
            self.database.add_message(message)
            self._append_message(message)
            self.status_label.setText(f"در حال ارسال «{file_path.name}»…")
        except (OSError, ValueError) as error:
            _show_message(self, QMessageBox.Icon.Warning, "فایل ارسال نشد", str(error))

    def _open_downloads(self) -> None:
        try:
            self.downloads_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            _show_message(
                self,
                QMessageBox.Icon.Warning,
                "پوشه در دسترس نیست",
                "ساخت پوشهٔ دریافت فایل‌ها انجام نشد.",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.downloads_dir.resolve()))):
            _show_message(
                self,
                QMessageBox.Icon.Warning,
                "بازکردن پوشه ناموفق بود",
                f"پوشهٔ دریافت فایل‌ها را دستی باز کنید:\n{self.downloads_dir}",
            )

    def _open_message_link(self, url: QUrl) -> None:
        if url.scheme() != "file":
            return
        file_path = Path(url.toLocalFile())
        if not file_path.is_file():
            _show_message(
                self,
                QMessageBox.Icon.Warning,
                "فایل پیدا نشد",
                f"این فایل دیگر در مسیر زیر موجود نیست:\n{file_path}",
            )
            return
        if not QDesktopServices.openUrl(url):
            _show_message(
                self,
                QMessageBox.Icon.Warning,
                "بازکردن فایل ناموفق بود",
                f"فایل را دستی باز کنید:\n{file_path}",
            )

    def _reveal_file(self, file_path: str) -> None:
        path = Path(file_path)
        if not path.is_file():
            _show_message(
                self,
                QMessageBox.Icon.Warning,
                "فایل پیدا نشد",
                f"این فایل دیگر در مسیر زیر موجود نیست:\n{path}",
            )
            return
        try:
            if not QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(path.resolve().parent))
            ):
                raise OSError("بازکردن پوشه انجام نشد.")
        except OSError as error:
            _show_message(
                self,
                QMessageBox.Icon.Warning,
                "نمایش فایل در پوشه ناموفق بود",
                f"پوشهٔ فایل را دستی باز کنید:\n{path.parent}\n{error}",
            )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() and Path(url.toLocalFile()).is_file()
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        file_paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile() and Path(url.toLocalFile()).is_file()
        ]
        if not file_paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self._send_dropped_files(file_paths)

    def _send_dropped_files(self, file_paths: list[Path]) -> None:
        if self._active_peer_id is None:
            _show_message(
                self,
                QMessageBox.Icon.Information,
                "گفت‌وگوی خصوصی لازم است",
                "فایل را پس از انتخاب گفت‌وگوی خصوصی کاربر، روی پنجره رها کنید.",
            )
            return
        for file_path in file_paths:
            self._confirm_file_send(file_path)

    def _on_composer_changed(self) -> None:
        if self._active_peer_id is None or not self.composer.toPlainText().strip():
            return
        now = time.monotonic()
        if now - self._last_typing_at >= 1.0:
            self._last_typing_at = now
            self.network.send_typing(self._active_peer_id)

    def _handle_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind in ("peer_online", "peer_updated"):
            self._add_peer(event)
            return
        if kind == "peer_offline":
            self._remove_peer(event["user_id"])
            return
        if kind == "typing":
            if event["user_id"] == self._active_peer_id:
                self.status_label.setText(f"{event['username']} در حال نوشتن است…")
                self._typing_timer.start(2500)
            return
        if kind == "error":
            if event.get("transfer_type") == "file":
                self.status_label.setText(
                    f"ارسال فایل ناموفق بود: {event.get('message', 'خطای شبکه')}"
                )
            else:
                self.status_label.setText(str(event.get("message", "خطای شبکه")))
            return

        if kind == "public_message":
            message = {
                "message_id": event["message_id"],
                "kind": "public",
                "sender_id": event["user_id"],
                "sender_name": event["username"],
                "body": event["body"],
                "timestamp": event["timestamp"],
                "outgoing": False,
            }
            self.database.add_message(message)
            self._mark_unread(None)
            if self._active_peer_id is None:
                self._append_message(message)
            self._notify(event["username"], event["body"], None)
        elif kind == "direct_message":
            self._add_offline_contact(
                {"user_id": event["user_id"], "username": event["username"]}
            )
            message = {
                "message_id": event["message_id"],
                "kind": "private",
                "sender_id": event["user_id"],
                "sender_name": event["username"],
                "recipient_id": self.profile["user_id"],
                "recipient_name": self.profile["display_name"],
                "body": event["body"],
                "timestamp": event["timestamp"],
                "outgoing": False,
            }
            self.database.add_message(message)
            self._mark_unread(event["user_id"])
            if self._active_peer_id == event["user_id"]:
                self._append_message(message)
            self._notify(event["username"], event["body"], event["user_id"])
        elif kind in ("file_received", "file_sent"):
            sent = kind == "file_sent"
            if sent:
                self.status_label.setText(f"فایل ارسال شد: {event['filename']}")
                return
            message = {
                "message_id": event["message_id"],
                "kind": "file",
                "sender_id": event["user_id"],
                "sender_name": event["username"],
                "recipient_id": self.profile["user_id"],
                "recipient_name": self.profile["display_name"],
                "body": event["filename"],
                "file_path": event["file_path"],
                "timestamp": event["timestamp"],
                "outgoing": False,
            }
            self._add_offline_contact(
                {"user_id": event["user_id"], "username": event["username"]}
            )
            self.database.add_message(message)
            self._mark_unread(event.get("user_id"))
            if event.get("user_id") == self._active_peer_id:
                self._append_message(message)
            self._notify(
                event["username"],
                f"فایلی برایتان فرستاد: {event['filename']}",
                event["user_id"],
            )
            self.status_label.setText(f"فایل در این مسیر ذخیره شد: {self.downloads_dir}")

    def _notify(self, title: str, message: str, peer_id: str | None) -> None:
        notification_title = f"AtiChat — {title}"
        self._notification_target = (peer_id, title)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.showMessage(
                notification_title,
                message[:200],
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
            return
        QApplication.beep()
        try:
            from plyer import notification
        except ImportError:
            self.status_label.setText("اعلان دسکتاپ در دسترس نیست؛ صدای هشدار پخش شد.")
            return
        try:
            notification.notify(
                title=notification_title,
                message=message[:200],
                app_name="AtiChat",
                timeout=5,
            )
        except (OSError, RuntimeError):
            self.status_label.setText("نمایش اعلان دسکتاپ انجام نشد.")

    def _open_notification_target(self) -> None:
        target = self._notification_target
        if target is not None:
            peer_id, username = target
            if peer_id is None:
                item = self.user_list.item(0)
            else:
                item = self._find_item(peer_id)
                if item is None:
                    self._add_offline_contact(
                        {"user_id": peer_id, "username": username}
                    )
                    item = self._find_item(peer_id)
            if item is not None:
                self.user_list.setCurrentItem(item)
        self._show_from_tray()

    def _on_tray_activated(
        self, reason: QSystemTrayIcon.ActivationReason
    ) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_from_tray()

    def _refresh_online_peers(self) -> None:
        peers = self.network.peers()
        online_ids = {peer["user_id"] for peer in peers}
        for peer_id in list(self._online):
            if peer_id not in online_ids:
                self._remove_peer(peer_id)
        for peer in peers:
            self._add_peer(peer)
        self._update_connection_label()

    def _refresh_users(self) -> None:
        self.network.announce_presence()
        self._refresh_online_peers()
        self.connection_label.setText("اعلان حضور فرستاده شد؛ در حال جست‌وجوی کاربران…")
        QTimer.singleShot(3000, self._refresh_online_peers)

    def _update_connection_label(self) -> None:
        self.connection_label.setText(
            f"{_persian_digits(str(len(self._online)))} کاربر برخط"
            if self._online
            else "کاربر دیگری برخط نیست"
        )

    def _show_from_tray(self) -> None:
        self._refresh_online_peers()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._allow_close = True
        self.tray_icon.hide()
        self.close()
        QApplication.quit()

    def _stop_network(self) -> None:
        if self._network_stopped:
            return
        self._network_stopped = True
        self.network.stop()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.profile, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        display_name = dialog.name_input.text().strip()
        if not display_name:
            _show_message(
                self,
                QMessageBox.Icon.Warning,
                "نام لازم است",
                "لطفاً یک نام نمایشی وارد کنید.",
            )
            return
        startup_enabled = dialog.startup_checkbox.isChecked()
        if startup_enabled or startup_enabled != bool(self.profile["startup_enabled"]):
            try:
                _set_windows_startup(startup_enabled)
            except OSError:
                _show_message(
                    self,
                    QMessageBox.Icon.Warning,
                    "ذخیرهٔ تنظیم انجام نشد",
                    "تغییر تنظیم اجرای خودکار در ویندوز ممکن نیست.",
                )
                return
            except RuntimeError as error:
                _show_message(
                    self,
                    QMessageBox.Icon.Warning,
                    "تنظیم در دسترس نیست",
                    str(error),
                )
                return
        self.profile["display_name"] = display_name
        self.profile["avatar"] = dialog.avatar
        self.profile["startup_enabled"] = int(startup_enabled)
        self.database.save_profile(
            self.profile["user_id"],
            display_name,
            dialog.avatar,
            startup_enabled,
        )
        self.network.set_profile(display_name, dialog.avatar)
        self.profile_label.setText(f"AtiChat · {display_name}")
        if self._active_peer_id is None:
            self._load_history()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._allow_close and self.tray_icon.isVisible():
            event.ignore()
            self.hide()
            return
        self.tray_icon.hide()
        self._stop_network()
        super().closeEvent(event)
