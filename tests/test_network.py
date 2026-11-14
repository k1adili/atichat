import json
import socket
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from network import NetworkManager


def _available_port(socket_type: int) -> int:
    with socket.socket(socket.AF_INET, socket_type) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class NetworkTests(unittest.TestCase):
    def test_presence_updates_and_peer_expiration_emit_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events: list[dict] = []
            manager = NetworkManager(
                user_id="local-user",
                username="Local",
                avatar="",
                callback=events.append,
                data_dir=Path(temporary) / "data",
                downloads_dir=Path(temporary) / "downloads",
                udp_port=_available_port(socket.SOCK_DGRAM),
                tcp_port=_available_port(socket.SOCK_STREAM),
                broadcast_address="127.0.0.1",
            )
            presence = {
                "type": "presence",
                "user_id": "peer-id",
                "username": "Old name",
                "avatar": "",
                "tcp_port": 37021,
                "fingerprint": "a" * 64,
            }

            manager._handle_udp(presence, ("127.0.0.1", manager.udp_port))
            self.assertEqual(events[-1]["type"], "peer_online")

            updated_presence = {**presence, "username": "New name"}
            manager._handle_udp(
                updated_presence, ("127.0.0.1", manager.udp_port)
            )
            self.assertEqual(events[-1]["type"], "peer_updated")
            self.assertEqual(events[-1]["username"], "New name")

            manager._expire_peers(time.monotonic() + 11)
            self.assertEqual(events[-1]["type"], "peer_offline")

            manager._handle_udp(
                updated_presence, ("127.0.0.1", manager.udp_port)
            )
            self.assertEqual(events[-1]["type"], "peer_online")

    def test_presence_announcement_does_not_trigger_another_announcement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = NetworkManager(
                user_id="local-user",
                username="Local",
                avatar="",
                callback=lambda _event: None,
                data_dir=Path(temporary) / "data",
                downloads_dir=Path(temporary) / "downloads",
                udp_port=_available_port(socket.SOCK_DGRAM),
                tcp_port=_available_port(socket.SOCK_STREAM),
                broadcast_address="127.0.0.1",
            )
            presence = {
                "type": "presence",
                "user_id": "peer-id",
                "username": "Peer",
                "avatar": "",
                "tcp_port": 37021,
                "fingerprint": "a" * 64,
            }

            with patch.object(manager, "_send_udp") as send_udp:
                manager._handle_udp(presence, ("127.0.0.1", manager.udp_port))

            send_udp.assert_not_called()

    def test_presence_request_gets_one_direct_announcement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = NetworkManager(
                user_id="local-user",
                username="Local",
                avatar="",
                callback=lambda _event: None,
                data_dir=Path(temporary) / "data",
                downloads_dir=Path(temporary) / "downloads",
                udp_port=_available_port(socket.SOCK_DGRAM),
                tcp_port=_available_port(socket.SOCK_STREAM),
                broadcast_address="127.0.0.1",
            )

            with patch.object(manager, "_send_udp") as send_udp:
                manager._handle_udp(
                    {"type": "presence_request", "user_id": "peer-id"},
                    ("127.0.0.1", manager.udp_port),
                )

            send_udp.assert_called_once_with(
                manager._presence_packet(),
                ("127.0.0.1", manager.udp_port),
            )

    def test_udp_loop_expires_peers_between_heartbeats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events: list[dict] = []
            manager = NetworkManager(
                user_id="local-user",
                username="Local",
                avatar="",
                callback=events.append,
                data_dir=Path(temporary) / "data",
                downloads_dir=Path(temporary) / "downloads",
                udp_port=_available_port(socket.SOCK_DGRAM),
                tcp_port=_available_port(socket.SOCK_STREAM),
                broadcast_address="127.0.0.1",
            )
            receive_count = 0

            def receive(_size: int) -> tuple[bytes, tuple[str, int]]:
                nonlocal receive_count
                receive_count += 1
                if receive_count == 1:
                    manager._peers["peer-id"] = {
                        "user_id": "peer-id",
                        "last_seen": 89.0,
                    }
                    raise socket.timeout
                manager._stop.set()
                raise OSError

            manager._udp_socket = Mock()
            manager._udp_socket.recvfrom.side_effect = receive

            with patch("network.time.monotonic", side_effect=[100.0, 101.0]):
                manager._udp_loop()

            self.assertEqual([event["type"] for event in events], ["peer_offline"])
            self.assertEqual(manager.peers(), [])

    def test_peer_goes_offline_after_real_udp_heartbeat_stops(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            peer_online = threading.Event()
            peer_offline = threading.Event()
            events: list[dict] = []

            def on_event(event: dict) -> None:
                events.append(event)
                if event["type"] == "peer_online":
                    peer_online.set()
                elif event["type"] == "peer_offline":
                    peer_offline.set()

            udp_port = _available_port(socket.SOCK_DGRAM)
            manager = NetworkManager(
                user_id="local-user",
                username="Local",
                avatar="",
                callback=on_event,
                data_dir=Path(temporary) / "data",
                downloads_dir=Path(temporary) / "downloads",
                udp_port=udp_port,
                tcp_port=_available_port(socket.SOCK_STREAM),
                broadcast_address="127.0.0.1",
            )
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            presence = {
                "type": "presence",
                "user_id": "peer-id",
                "username": "Peer",
                "avatar": "",
                "tcp_port": 37021,
                "fingerprint": "a" * 64,
            }

            try:
                with patch("network.PEER_TIMEOUT_SECONDS", 1.5):
                    manager.start()
                    sender.sendto(
                        json.dumps(presence).encode("utf-8"),
                        ("127.0.0.1", udp_port),
                    )

                    self.assertTrue(peer_online.wait(timeout=2))
                    self.assertTrue(peer_offline.wait(timeout=3))

                self.assertEqual(
                    [event["type"] for event in events],
                    ["peer_online", "peer_offline"],
                )
            finally:
                sender.close()
                manager.stop()

    def test_stop_does_not_broadcast_unnegotiated_packet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = NetworkManager(
                user_id="local-user",
                username="Local",
                avatar="",
                callback=lambda _event: None,
                data_dir=Path(temporary) / "data",
                downloads_dir=Path(temporary) / "downloads",
                udp_port=_available_port(socket.SOCK_DGRAM),
                tcp_port=_available_port(socket.SOCK_STREAM),
                broadcast_address="127.0.0.1",
            )
            manager.start()
            try:
                with patch.object(manager, "_broadcast") as broadcast:
                    manager.stop()
                broadcast.assert_not_called()
            finally:
                manager.stop()

    def test_announce_presence_broadcasts_current_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = NetworkManager(
                user_id="local-user",
                username="Local",
                avatar="avatar-data",
                callback=lambda _event: None,
                data_dir=Path(temporary) / "data",
                downloads_dir=Path(temporary) / "downloads",
                udp_port=_available_port(socket.SOCK_DGRAM),
                tcp_port=_available_port(socket.SOCK_STREAM),
                broadcast_address="127.0.0.1",
            )

            with patch.object(manager, "_broadcast") as broadcast:
                manager.announce_presence()

            self.assertEqual(broadcast.call_count, 2)
            packet = broadcast.call_args_list[0].args[0]
            self.assertEqual(packet["type"], "presence")
            self.assertEqual(packet["user_id"], "local-user")
            self.assertEqual(packet["username"], "Local")
            self.assertEqual(packet["avatar"], "avatar-data")
            self.assertEqual(
                broadcast.call_args_list[1].args[0],
                {"type": "presence_request", "user_id": "local-user"},
            )

    def test_tls_direct_message_and_file_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            received: list[dict] = []
            received_condition = threading.Condition()
            alice_id = str(uuid.uuid4())
            bob_id = str(uuid.uuid4())

            def on_bob_event(event: dict) -> None:
                with received_condition:
                    received.append(event)
                    received_condition.notify_all()

            bob = NetworkManager(
                user_id=bob_id,
                username="Bob",
                avatar="",
                callback=on_bob_event,
                data_dir=root / "bob-data",
                downloads_dir=root / "downloads",
                udp_port=_available_port(socket.SOCK_DGRAM),
                tcp_port=_available_port(socket.SOCK_STREAM),
                broadcast_address="127.0.0.1",
            )
            alice = NetworkManager(
                user_id=alice_id,
                username="Alice",
                avatar="",
                callback=lambda _event: None,
                data_dir=root / "alice-data",
                downloads_dir=root / "alice-downloads",
                udp_port=_available_port(socket.SOCK_DGRAM),
                tcp_port=_available_port(socket.SOCK_STREAM),
                broadcast_address="127.0.0.1",
            )
            try:
                bob.start()
                alice.start()
                alice._peers[bob_id] = {
                    "user_id": bob_id,
                    "username": "Bob",
                    "ip": "127.0.0.1",
                    "tcp_port": bob.tcp_port,
                    "fingerprint": bob._fingerprint,
                    "last_seen": time.monotonic(),
                }

                source_file = root / "sample.txt"
                source_file.write_bytes(b"file contents")
                alice.send_direct(
                    bob_id,
                    "message before file",
                    str(uuid.uuid4()),
                    "2026-01-01T10:00:00+00:00",
                )
                alice.send_file(bob_id, source_file)
                alice.send_direct(
                    bob_id,
                    "message after file",
                    str(uuid.uuid4()),
                    "2026-01-01T09:00:00+00:00",
                )

                with received_condition:
                    self.assertTrue(
                        received_condition.wait_for(
                            lambda: len(
                                [
                                    item
                                    for item in received
                                    if item["type"] in ("direct_message", "file_received")
                                ]
                            )
                            == 3,
                            timeout=5,
                        )
                    )
                transfer_events = [
                    item
                    for item in received
                    if item["type"] in ("direct_message", "file_received")
                ]
                self.assertEqual(
                    [item["type"] for item in transfer_events],
                    ["direct_message", "file_received", "direct_message"],
                )
                self.assertEqual(transfer_events[0]["body"], "message before file")
                self.assertEqual(transfer_events[2]["body"], "message after file")
                file_event = transfer_events[1]
                self.assertEqual(Path(file_event["file_path"]).read_bytes(), b"file contents")
            finally:
                alice.stop()
                bob.stop()


if __name__ == "__main__":
    unittest.main()
