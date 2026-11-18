import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from http_api import network
from http_api.handlers import connection


def completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TailscaleIpTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(network, "_is_tailscale_process_alive", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_closed_app_is_never_launched(self):
        with mock.patch.object(network, "_is_tailscale_process_alive", return_value=False), \
             mock.patch.object(network, "_run_tailscale") as run:
            self.assertIsNone(network.get_tailscale_ip())
        run.assert_not_called()

    def test_running_returns_tailnet_ip(self):
        replies = {"status": completed(0), "ip": completed(0, "100.78.44.4\n")}
        with mock.patch.object(network, "_run_tailscale", side_effect=lambda *a: replies[a[0]]):
            self.assertEqual(network.get_tailscale_ip(), "100.78.44.4")

    def test_stopped_returns_none_even_though_ip_prints(self):
        replies = {"status": completed(1), "ip": completed(0, "100.78.44.4\n")}
        with mock.patch.object(network, "_run_tailscale", side_effect=lambda *a: replies[a[0]]):
            self.assertIsNone(network.get_tailscale_ip())

    def test_missing_cli_returns_none(self):
        with mock.patch.object(network, "_tailscale_cli", return_value=None):
            self.assertIsNone(network.get_tailscale_ip())


class ConnectionEndpointTest(unittest.TestCase):
    def _addresses(self, lan_ip, tailscale_ip):
        handler = SimpleNamespace(server=SimpleNamespace(server_address=("0.0.0.0", 8765)))
        sent = {}
        with mock.patch.object(connection, "get_local_lan_ip", return_value=lan_ip), \
             mock.patch.object(connection, "get_tailscale_ip", return_value=tailscale_ip), \
             mock.patch.object(connection, "send_json", side_effect=lambda h, d, *a, **k: sent.update(d)):
            connection.handle_get_connection(handler, {}, {})
        return sent["addresses"]

    def test_lan_first_then_tailscale(self):
        self.assertEqual(self._addresses("192.168.31.117", "100.78.44.4"),
                         ["http://192.168.31.117:8765", "http://100.78.44.4:8765"])

    def test_loopback_and_missing_tailscale_are_left_out(self):
        self.assertEqual(self._addresses("127.0.0.1", None), [])


if __name__ == "__main__":
    unittest.main()
