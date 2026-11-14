"""LAN discovery, UDP public chat, and TLS-protected direct transfers."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import re
import socket
import ssl
import struct
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

UDP_PORT = 37020
TCP_PORT = 37021
HEARTBEAT_SECONDS = 3
PEER_TIMEOUT_SECONDS = 10
MAX_UDP_PACKET = 60_000
MAX_TCP_HEADER = 16_384
MAX_MESSAGE_LENGTH = 12_000
MAX_FILE_SIZE = 100 * 1024 * 1024
FILE_CHUNK_SIZE = 64 * 1024
TRANSFER_ACK = b"\x06"

EventCallback = Callable[[dict[str, Any]], None]


def _system_error_code(error: OSError) -> int | None:
    return getattr(error, "winerror", None) or error.errno


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _make_tls_files(certificate_path: Path, key_path: Path) -> None:
    """Create a stable self-signed certificate used for TLS fingerprint pinning."""
    certificate_path.parent.mkdir(parents=True, exist_ok=True)
    if certificate_path.exists() and key_path.exists():
        return

    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "K1chat LAN peer")]
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


class NetworkManager:
    """Owns the LAN sockets and dispatches network events to the caller."""

    def __init__(
        self,
        user_id: str,
        username: str,
        avatar: str,
        callback: EventCallback,
        data_dir: str | Path,
        downloads_dir: str | Path,
        udp_port: int = UDP_PORT,
        tcp_port: int = TCP_PORT,
        broadcast_address: str = "255.255.255.255",
    ) -> None:
        self.user_id = user_id
        self.username = username
        self.avatar = avatar
        self.callback = callback
        self.udp_port = udp_port
        self.tcp_port = tcp_port
        self.broadcast_address = broadcast_address
        self.downloads_dir = Path(downloads_dir)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        data_path = Path(data_dir)
        certificate_path = data_path / "peer-cert.pem"
        key_path = data_path / "peer-key.pem"
        _make_tls_files(certificate_path, key_path)

        self._server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._server_context.load_cert_chain(certificate_path, key_path)
        cert_der = ssl.PEM_cert_to_DER_cert(certificate_path.read_text(encoding="ascii"))
        self._fingerprint = hashlib.sha256(cert_der).hexdigest()

        self._client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._client_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._client_context.check_hostname = False
        self._client_context.verify_mode = ssl.CERT_NONE
        self._stop = threading.Event()
        self._peers: dict[str, dict[str, Any]] = {}
        self._peers_lock = threading.Lock()
        self._udp_socket: socket.socket | None = None
        self._tcp_socket: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._sender_lock = threading.Lock()
        self._sender_queues: dict[
            str,
            queue.Queue[
                tuple[Callable[..., None], dict[str, Any], dict[str, Any], Path | None]
                | None
            ],
        ] = {}

    def start(self) -> None:
        if self._udp_socket is not None:
            return
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            udp.bind(("", self.udp_port))
            udp.settimeout(0.5)
            tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tcp.bind(("", self.tcp_port))
            tcp.listen(16)
            tcp.settimeout(0.5)
        except OSError:
            udp.close()
            tcp.close()
            raise

        self._udp_socket, self._tcp_socket = udp, tcp
        self._threads = [
            threading.Thread(target=self._udp_loop, name="AtiChat-UDP", daemon=True),
            threading.Thread(target=self._tcp_loop, name="AtiChat-TCP", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        self.announce_presence()

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        with self._sender_lock:
            sender_queues = list(self._sender_queues.values())
            self._sender_queues.clear()
        for sender_queue in sender_queues:
            sender_queue.put(None)
        if self._udp_socket is not None:
            self._udp_socket.close()
            self._udp_socket = None
        if self._tcp_socket is not None:
            self._tcp_socket.close()
            self._tcp_socket = None
        for thread in self._threads:
            thread.join(timeout=1)
        self._threads.clear()

    def set_profile(self, username: str, avatar: str) -> None:
        self.username = username
        self.avatar = avatar
        self._announce()

    def announce_presence(self) -> None:
        """Immediately announce this user and prompt peers to announce themselves."""
        self._announce()
        self._broadcast(
            {"type": "presence_request", "user_id": self.user_id}
        )

    def peers(self) -> list[dict[str, Any]]:
        with self._peers_lock:
            return [dict(peer) for peer in self._peers.values()]

    def send_public(self, body: str, message_id: str, timestamp: str) -> None:
        packet = {
            "type": "public_message",
            "user_id": self.user_id,
            "username": self.username,
            "message_id": message_id,
            "timestamp": timestamp,
            "body": body[:MAX_MESSAGE_LENGTH],
        }
        self._broadcast(packet)

    def send_typing(self, peer_id: str) -> None:
        self._send_peer_udp(
            peer_id,
            {
                "type": "typing",
                "user_id": self.user_id,
                "username": self.username,
            },
        )

    def send_direct(
        self, peer_id: str, body: str, message_id: str, timestamp: str
    ) -> None:
        peer = self._get_peer(peer_id)
        packet = {
            "type": "direct_message",
            "user_id": self.user_id,
            "username": self.username,
            "recipient_id": peer_id,
            "message_id": message_id,
            "timestamp": timestamp,
            "body": body[:MAX_MESSAGE_LENGTH],
        }
        self._start_sender(self._send_tcp, peer, packet, None)

    def send_file(self, peer_id: str, file_path: str | Path) -> dict[str, Any]:
        path = Path(file_path)
        size = path.stat().st_size
        if not path.is_file():
            raise ValueError("مورد انتخاب‌شده یک فایل معتبر نیست.")
        if size > MAX_FILE_SIZE:
            raise ValueError(
                f"حداکثر اندازهٔ فایل باید {MAX_FILE_SIZE // (1024 * 1024)} مگابایت باشد."
            )
        peer = self._get_peer(peer_id)
        packet = {
            "type": "file",
            "user_id": self.user_id,
            "username": self.username,
            "recipient_id": peer_id,
            "message_id": str(uuid.uuid4()),
            "timestamp": _utc_now(),
            "filename": path.name,
            "size": size,
        }
        self._start_sender(self._send_tcp, peer, packet, path)
        return packet

    def _start_sender(
        self,
        target: Callable[..., None],
        peer: dict[str, Any],
        packet: dict[str, Any],
        path: Path | None,
    ) -> None:
        peer_id = peer["user_id"]
        with self._sender_lock:
            sender_queue = self._sender_queues.get(peer_id)
            if sender_queue is None:
                sender_queue = queue.Queue()
                self._sender_queues[peer_id] = sender_queue
                sender_thread = threading.Thread(
                    target=self._sender_loop,
                    args=(sender_queue,),
                    name=f"AtiChat-send-{peer_id[:8]}",
                    daemon=True,
                )
                sender_thread.start()
            sender_queue.put((target, peer, packet, path))

    @staticmethod
    def _sender_loop(
        sender_queue: queue.Queue[
            tuple[Callable[..., None], dict[str, Any], dict[str, Any], Path | None]
            | None
        ],
    ) -> None:
        while True:
            job = sender_queue.get()
            if job is None:
                return
            target, peer, packet, path = job
            target(peer, packet, path)

    def _get_peer(self, peer_id: str) -> dict[str, Any]:
        with self._peers_lock:
            peer = self._peers.get(peer_id)
        if peer is None:
            raise ValueError("این کاربر دیگر برخط نیست.")
        return dict(peer)

    def _send_tcp(
        self, peer: dict[str, Any], packet: dict[str, Any], path: Path | None
    ) -> None:
        try:
            with socket.create_connection(
                (peer["ip"], peer["tcp_port"]), timeout=8
            ) as raw_socket:
                with self._client_context.wrap_socket(
                    raw_socket, server_hostname="K1chat LAN peer"
                ) as tls_socket:
                    remote_certificate = tls_socket.getpeercert(binary_form=True)
                    actual_fingerprint = hashlib.sha256(remote_certificate).hexdigest()
                    if actual_fingerprint != peer.get("fingerprint"):
                        raise ssl.SSLError("گواهی امنیتی همتا با اطلاعات شناسایی شبکه مطابقت ندارد.")
                    self._send_frame(tls_socket, packet)
                    if path is not None:
                        with path.open("rb") as source:
                            while chunk := source.read(FILE_CHUNK_SIZE):
                                tls_socket.sendall(chunk)
                    if self._receive_exact(tls_socket, len(TRANSFER_ACK)) != TRANSFER_ACK:
                        raise ConnectionError("همتا دریافت پیام را تأیید نکرد.")
            if packet["type"] == "file":
                self._emit(
                    {
                        "type": "file_sent",
                        "peer_id": peer["user_id"],
                        "peer_name": peer["username"],
                        "message_id": packet["message_id"],
                        "timestamp": packet["timestamp"],
                        "file_path": str(path),
                        "filename": packet["filename"],
                    }
                )
        except (OSError, ssl.SSLError, ValueError) as error:
            error_code = _system_error_code(error) if isinstance(error, OSError) else None
            detail = f" (کد خطا: {error_code})" if error_code else ""
            self._emit(
                {
                    "type": "error",
                    "message": f"ارسال به {peer['username']} ناموفق بود.{detail}",
                    "message_id": packet["message_id"],
                    "peer_id": peer["user_id"],
                    "transfer_type": packet["type"],
                }
            )

    @staticmethod
    def _send_frame(sock: ssl.SSLSocket, packet: dict[str, Any]) -> None:
        payload = json.dumps(packet, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_TCP_HEADER:
            raise ValueError("اطلاعات همراه پیام بیش از حد مجاز است.")
        sock.sendall(struct.pack("!I", len(payload)) + payload)

    @staticmethod
    def _receive_exact(sock: ssl.SSLSocket, count: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < count:
            chunk = sock.recv(min(FILE_CHUNK_SIZE, count - len(chunks)))
            if not chunk:
                raise ConnectionError("ارتباط از سوی همتا به‌طور غیرمنتظره قطع شد.")
            chunks.extend(chunk)
        return bytes(chunks)

    def _tcp_loop(self) -> None:
        while not self._stop.is_set():
            listener = self._tcp_socket
            if listener is None:
                break
            try:
                client, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    self._emit(
                        {
                            "type": "error",
                            "message": "شنود ارتباط مستقیم متوقف شد.",
                        }
                    )
                break
            threading.Thread(
                target=self._handle_tcp,
                args=(client, address),
                name="AtiChat-receive",
                daemon=True,
            ).start()

    def _handle_tcp(self, raw_socket: socket.socket, address: tuple[str, int]) -> None:
        try:
            raw_socket.settimeout(30)
            with self._server_context.wrap_socket(raw_socket, server_side=True) as tls_socket:
                header_length = struct.unpack("!I", self._receive_exact(tls_socket, 4))[0]
                if not 1 <= header_length <= MAX_TCP_HEADER:
                    raise ValueError("طول سرآیند پیام معتبر نیست.")
                packet = json.loads(self._receive_exact(tls_socket, header_length).decode("utf-8"))
                if not isinstance(packet, dict):
                    raise ValueError("ساختار پیام معتبر نیست.")
                if packet.get("recipient_id") != self.user_id:
                    raise ValueError("این پیام یا فایل برای این کاربر ارسال نشده است.")
                if not isinstance(packet.get("user_id"), str) or not isinstance(packet.get("username"), str):
                    raise ValueError("مشخصات فرستنده معتبر نیست.")
                kind = packet.get("type")
                if kind == "direct_message":
                    body = packet.get("body")
                    if (
                        not isinstance(body, str)
                        or len(body) > MAX_MESSAGE_LENGTH
                        or not isinstance(packet.get("message_id"), str)
                        or not isinstance(packet.get("timestamp"), str)
                    ):
                        raise ValueError("متن پیام مستقیم معتبر نیست.")
                    self._emit(
                        {
                            "type": "direct_message",
                            "user_id": packet["user_id"],
                            "username": packet["username"][:64],
                            "message_id": str(packet["message_id"]),
                            "timestamp": str(packet["timestamp"]),
                            "body": body,
                            "ip": address[0],
                        }
                    )
                elif kind == "file":
                    self._receive_file(tls_socket, packet, address[0])
                else:
                    raise ValueError("نوع پیام دریافتی پشتیبانی نمی‌شود.")
                tls_socket.sendall(TRANSFER_ACK)
        except (
            OSError,
            ssl.SSLError,
            ValueError,
            UnicodeError,
            ConnectionError,
            KeyError,
            TypeError,
        ) as error:
            if not self._stop.is_set():
                code = _system_error_code(error) if isinstance(error, OSError) else None
                detail = f" (کد خطا: {code})" if code else ""
                self._emit(
                    {
                        "type": "error",
                        "message": f"دریافت پیام یا فایل از {address[0]} ناموفق بود.{detail}",
                    }
                )
        finally:
            try:
                raw_socket.close()
            except OSError:
                pass

    def _receive_file(
        self, sock: ssl.SSLSocket, packet: dict[str, Any], peer_ip: str
    ) -> None:
        size = packet.get("size")
        filename = packet.get("filename")
        if not isinstance(size, int) or not 0 <= size <= MAX_FILE_SIZE:
            raise ValueError("اندازهٔ فایل دریافتی معتبر نیست.")
        if not isinstance(filename, str) or not filename or len(filename) > 255:
            raise ValueError("نام فایل دریافتی معتبر نیست.")
        if not isinstance(packet.get("message_id"), str) or not isinstance(packet.get("timestamp"), str):
            raise ValueError("اطلاعات همراه فایل دریافتی معتبر نیست.")
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(filename).name).strip(" .")
        if not safe_name:
            safe_name = "received-file"
        destination = self.downloads_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        temporary = destination.with_name(destination.name + ".part")
        remaining = size
        try:
            with temporary.open("xb") as output:
                while remaining:
                    chunk = self._receive_exact(sock, min(FILE_CHUNK_SIZE, remaining))
                    output.write(chunk)
                    remaining -= len(chunk)
            temporary.replace(destination)
        except (OSError, ConnectionError):
            temporary.unlink(missing_ok=True)
            raise
        self._emit(
            {
                "type": "file_received",
                "user_id": packet["user_id"],
                "username": packet["username"][:64],
                "message_id": str(packet["message_id"]),
                "timestamp": str(packet["timestamp"]),
                "filename": safe_name,
                "file_path": str(destination),
                "size": size,
                "ip": peer_ip,
            }
        )

    def _udp_loop(self) -> None:
        next_heartbeat = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_heartbeat:
                self._announce()
                next_heartbeat = now + HEARTBEAT_SECONDS
            self._expire_peers(now)
            udp = self._udp_socket
            if udp is None:
                break
            try:
                payload, address = udp.recvfrom(MAX_UDP_PACKET)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                packet = json.loads(payload.decode("utf-8"))
                if isinstance(packet, dict):
                    self._handle_udp(packet, address)
            except (UnicodeError, json.JSONDecodeError, ValueError):
                continue

    def _handle_udp(self, packet: dict[str, Any], address: tuple[str, int]) -> None:
        kind = packet.get("type")
        peer_id = packet.get("user_id")
        if not isinstance(peer_id, str) or peer_id == self.user_id:
            return
        if kind == "presence_request":
            self._send_udp(
                self._presence_packet(),
                (address[0], self.udp_port),
            )
        elif kind == "presence":
            username = packet.get("username")
            tcp_port = packet.get("tcp_port")
            fingerprint = packet.get("fingerprint")
            avatar = packet.get("avatar", "")
            if (
                not isinstance(username, str)
                or not username.strip()
                or not isinstance(tcp_port, int)
                or not 1 <= tcp_port <= 65535
                or not isinstance(fingerprint, str)
                or not re.fullmatch(r"[a-f0-9]{64}", fingerprint)
                or not isinstance(avatar, str)
                or len(avatar) > 16_000
            ):
                return
            peer = {
                "user_id": peer_id,
                "username": username[:64],
                "avatar": avatar,
                "ip": address[0],
                "tcp_port": tcp_port,
                "fingerprint": fingerprint,
                "last_seen": time.monotonic(),
            }
            with self._peers_lock:
                previous = self._peers.get(peer_id)
                is_new = previous is None
                is_updated = previous is not None and any(
                    previous[key] != peer[key]
                    for key in (
                        "username",
                        "avatar",
                        "ip",
                        "tcp_port",
                        "fingerprint",
                    )
                )
                self._peers[peer_id] = peer
            if is_new:
                self._emit({"type": "peer_online", **peer})
            elif is_updated:
                self._emit({"type": "peer_updated", **peer})
        elif kind == "public_message":
            body = packet.get("body")
            username = packet.get("username")
            if (
                not isinstance(body, str)
                or len(body) > MAX_MESSAGE_LENGTH
                or not isinstance(username, str)
                or not isinstance(packet.get("message_id"), str)
                or not isinstance(packet.get("timestamp"), str)
            ):
                return
            self._emit(
                {
                    "type": "public_message",
                    "user_id": peer_id,
                    "username": username[:64],
                    "message_id": packet["message_id"],
                    "timestamp": packet["timestamp"],
                    "body": body,
                }
            )
        elif kind == "typing":
            peer = self._get_peer_silent(peer_id)
            if peer is not None:
                self._emit(
                    {
                        "type": "typing",
                        "user_id": peer_id,
                        "username": peer["username"],
                    }
                )

    def _get_peer_silent(self, peer_id: str) -> dict[str, Any] | None:
        with self._peers_lock:
            peer = self._peers.get(peer_id)
            return dict(peer) if peer else None

    def _expire_peers(self, now: float) -> None:
        expired: list[dict[str, Any]] = []
        with self._peers_lock:
            for peer_id, peer in list(self._peers.items()):
                if now - peer["last_seen"] > PEER_TIMEOUT_SECONDS:
                    expired.append(self._peers.pop(peer_id))
        for peer in expired:
            self._emit({"type": "peer_offline", **peer})

    def _presence_packet(self) -> dict[str, Any]:
        return {
            "type": "presence",
            "user_id": self.user_id,
            "username": self.username[:64],
            "avatar": self.avatar[:16_000],
            "tcp_port": self.tcp_port,
            "fingerprint": self._fingerprint,
        }

    def _announce(self) -> None:
        self._broadcast(self._presence_packet())

    def _broadcast(self, packet: dict[str, Any]) -> None:
        self._send_udp(packet, (self.broadcast_address, self.udp_port))

    def _send_peer_udp(self, peer_id: str, packet: dict[str, Any]) -> None:
        peer = self._get_peer_silent(peer_id)
        if peer is not None:
            self._send_udp(packet, (peer["ip"], self.udp_port))

    def _send_udp(self, packet: dict[str, Any], address: tuple[str, int]) -> None:
        udp = self._udp_socket
        if udp is None:
            return
        try:
            payload = json.dumps(packet, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if len(payload) > MAX_UDP_PACKET:
                raise ValueError("اندازهٔ بستهٔ شبکه از حد مجاز بیشتر است.")
            udp.sendto(payload, address)
        except (OSError, ValueError) as error:
            if not self._stop.is_set():
                code = _system_error_code(error) if isinstance(error, OSError) else None
                detail = f" (کد خطا: {code})" if code else ""
                self._emit(
                    {
                        "type": "error",
                        "message": f"خطا در ارتباط با شبکهٔ محلی.{detail}",
                    }
                )

    def _emit(self, event: dict[str, Any]) -> None:
        self.callback(event)


def encode_avatar(image_bytes: bytes) -> str:
    """Encode an already-resized avatar for bounded UDP presence packets."""
    return base64.b64encode(image_bytes).decode("ascii")
