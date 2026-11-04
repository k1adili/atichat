"""AtiChat application entry point."""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

from PyQt6.QtCore import QLockFile, Qt, QStandardPaths
from PyQt6.QtGui import QFont, QFontDatabase, QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from database import Database
from network import NetworkManager
from ui import ChatWindow, NetworkBridge, _set_windows_startup


def _show_startup_error(title: str, text: str) -> None:
    message_box = QMessageBox(QMessageBox.Icon.Critical, title, text)
    message_box.addButton("باشه", QMessageBox.ButtonRole.AcceptRole)
    message_box.exec()


def _portable_data_dir(application_dir: Path, legacy_data_dir: Path) -> Path:
    data_dir = application_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    portable_database = data_dir / "k1chat.db"
    legacy_database = legacy_data_dir / "k1chat.db"
    if not portable_database.exists() and legacy_database.is_file():
        source = sqlite3.connect(legacy_database, timeout=10)
        destination = sqlite3.connect(portable_database, timeout=10)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

    for filename in ("peer-cert.pem", "peer-key.pem"):
        source_file = legacy_data_dir / filename
        destination_file = data_dir / filename
        if source_file.is_file() and not destination_file.exists():
            shutil.copy2(source_file, destination_file)
    return data_dir


def _acquire_instance_lock(temp_dir: Path) -> QLockFile:
    temp_dir.mkdir(parents=True, exist_ok=True)
    instance_lock = QLockFile(str(temp_dir / "K1Chat-single-instance.lock"))
    if not instance_lock.tryLock(0):
        if instance_lock.error() == QLockFile.LockError.LockFailedError:
            raise RuntimeError("برنامهٔ AtiChat در حال اجرا است.")
        raise OSError("قفل اجرای یکتای AtiChat در دسترس نیست.")
    return instance_lock


def main() -> int:
    app = QApplication(sys.argv)
    # Read the old AppData location before switching Qt's application identity.
    app.setOrganizationName("K1Chat")
    app.setApplicationName("K1Chat")
    legacy_data_dir = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
    )
    app.setApplicationName("AtiChat")
    app.setOrganizationName("AtiChat")
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    try:
        instance_lock = _acquire_instance_lock(
            Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.TempLocation
            ))
        )
    except RuntimeError as error:
        _show_startup_error("AtiChat در حال اجرا است", str(error))
        return 0
    except OSError as error:
        _show_startup_error("راه‌اندازی ناموفق", str(error))
        return 1

    app_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    application_dir = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    icon_path = app_root / "atichat.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    font_path = app_root / "assets" / "fonts" / "Vazirmatn.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id < 0:
        _show_startup_error(
            "فونت در دسترس نیست",
            "بارگذاری فونت Vazirmatn انجام نشد. فایل فونت برنامه را بررسی کنید.",
        )
        return 1
    app.setFont(QFont("Vazirmatn", 10))

    try:
        data_dir = _portable_data_dir(application_dir, legacy_data_dir)
        downloads_dir = application_dir / "Downloads" / "K1chat"
        downloads_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, sqlite3.Error) as error:
        _show_startup_error(
            "ذخیره‌سازی portable در دسترس نیست",
            f"برنامه نمی‌تواند در پوشهٔ خودش داده ذخیره کند:\n{error}",
        )
        return 1
    database = Database(data_dir / "k1chat.db")
    profile = database.get_profile()
    if sys.platform == "win32":
        try:
            _set_windows_startup(bool(profile["startup_enabled"]))
        except OSError as error:
            _show_startup_error(
                "همگام‌سازی اجرای خودکار ناموفق بود",
                f"تنظیم شروع AtiChat با مسیر فعلی برنامه همگام نشد:\n{error}",
            )
    bridge = NetworkBridge()

    try:
        network = NetworkManager(
            user_id=profile["user_id"],
            username=profile["display_name"],
            avatar=profile["avatar"],
            callback=bridge.event.emit,
            data_dir=data_dir,
            downloads_dir=downloads_dir,
        )
        network.start()
    except (OSError, RuntimeError, ValueError):
        _show_startup_error(
            "راه‌اندازی ناموفق",
            "AtiChat نتوانست پورت‌های شبکه را باز کند. "
            "بررسی کنید پورت‌های برنامه در حال استفاده نباشند.",
        )
        return 1

    window = ChatWindow(database, network, bridge, downloads_dir)
    window.show()
    app.aboutToQuit.connect(window._stop_network)
    result = app.exec()
    instance_lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
