"""Views for managing proxy and Tor settings."""

import logging
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from security.token_utils import require_admin
from .proxy_manager import (
    acquire_proxy_session,
    get_current_proxy_status,
    load_config,
    save_config,
    test_proxy_connection,
)

logger = logging.getLogger(__name__)


@api_view(["GET", "POST"])
def proxy_settings_view(request):
    """GET: Return current proxy settings and Tor status.
    POST: Update proxy settings.
    """
    denied = require_admin(request)
    if denied:
        return denied

    if request.method == "GET":
        return Response(get_current_proxy_status())

    # POST
    data = request.data or {}
    updated = save_config(data)
    return Response(get_current_proxy_status())


@api_view(["POST"])
def proxy_test_view(request):
    """Test connecting to an external IP check service using either:
    1. Explicit parameters from request body (`mode`, `tor_port`, `tor_path`, `custom_proxy_url`).
    2. Fallback to current config.
    """
    denied = require_admin(request)
    if denied:
        return denied

    data = request.data or {}
    cfg = load_config()

    mode = data.get("mode") or cfg.get("mode", "tor_auto")
    try:
        tor_port = int(data.get("tor_port") or cfg.get("tor_port", 9052))
    except (ValueError, TypeError):
        tor_port = 9052
    tor_path = (data.get("tor_path") if "tor_path" in data else cfg.get("tor_path", "")).strip()
    custom_proxy_url = (data.get("custom_proxy_url") or data.get("proxy_url") or cfg.get("custom_proxy_url", "")).strip()

    if mode == "custom":
        if not custom_proxy_url:
            return Response({"success": False, "error": "カスタムプロキシURLを入力してください"})
        result = test_proxy_connection(custom_proxy_url)
        return Response(result)

    # Tor auto mode: acquire Tor temporarily for testing
    from .proxy_manager import _tor_manager
    try:
        proxy_url = _tor_manager.acquire(tor_path=tor_path, port=tor_port, idle_timeout=15)
        try:
            result = test_proxy_connection(proxy_url)
            return Response(result)
        finally:
            _tor_manager.release(idle_timeout=15)
    except Exception as e:
        logger.exception("Tor proxy test failed: %s", e)
        return Response({"success": False, "error": str(e)})
