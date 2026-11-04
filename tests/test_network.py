import socket
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from network import NetworkManager


def _available_port(socket_type: int) -> int:
    with socket.socket(socket.AF_INET, socket_type) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class NetworkTests(unittest.TestCase):
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
