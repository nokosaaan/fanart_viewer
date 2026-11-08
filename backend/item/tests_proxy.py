"""Tests for proxy and Tor settings API and manager."""

from unittest.mock import patch, MagicMock
from django.test import TestCase
from rest_framework.test import APIClient
from security.token_utils import make_token

from .proxy_manager import (
    load_config,
    save_config,
    resolve_tor_binary,
    is_port_open,
    acquire_proxy_session,
    test_proxy_connection,
)


class ProxyManagerTests(TestCase):
    def test_load_and_save_config(self):
        cfg = save_config({
            "enabled": True,
            "mode": "custom",
            "custom_proxy_url": "socks5h://127.0.0.1:9050",
            "tor_port": 9055,
        })
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["mode"], "custom")
        self.assertEqual(cfg["custom_proxy_url"], "socks5h://127.0.0.1:9050")
        self.assertEqual(cfg["tor_port"], 9055)

        # Restore
        save_config({"enabled": False, "mode": "tor_auto"})

    def test_acquire_proxy_session_when_disabled(self):
        save_config({"enabled": False})
        with acquire_proxy_session() as proxy_url:
            self.assertIsNone(proxy_url)

    def test_acquire_proxy_session_custom(self):
        save_config({
            "enabled": True,
            "mode": "custom",
            "custom_proxy_url": "socks5h://127.0.0.1:9150",
        })
        with acquire_proxy_session() as proxy_url:
            self.assertEqual(proxy_url, "socks5h://127.0.0.1:9150")
        save_config({"enabled": False})

    @patch("item.proxy_manager.is_port_open", return_value=True)
    def test_acquire_proxy_session_tor_auto_already_open(self, mock_port):
        save_config({
            "enabled": True,
            "mode": "tor_auto",
            "tor_port": 9052,
        })
        with acquire_proxy_session() as proxy_url:
            self.assertEqual(proxy_url, "socks5h://127.0.0.1:9052")
        save_config({"enabled": False})


class ProxyApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        token = make_token('admin')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_get_settings(self):
        resp = self.client.get("/api/proxy_settings/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("config", data)
        self.assertIn("tor_installed", data)
        self.assertIn("tor_process_active", data)

    def test_post_settings(self):
        resp = self.client.post("/api/proxy_settings/", {
            "enabled": True,
            "mode": "custom",
            "custom_proxy_url": "socks5h://127.0.0.1:9150",
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["config"]["enabled"])
        self.assertEqual(data["config"]["mode"], "custom")

        # Clean up
        self.client.post("/api/proxy_settings/", {"enabled": False}, format="json")

    @patch("requests.get")
    def test_proxy_test_endpoint(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ip": "198.51.100.1"}
        mock_get.return_value = mock_resp

        resp = self.client.post("/api/proxy_settings/test/", {
            "proxy_url": "socks5h://127.0.0.1:9150",
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["ip"], "198.51.100.1")
