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
    1. A specified `proxy_url` in request body.
    2. The currently configured proxy mode (starts Tor temporarily if tor_auto).
    """
    denied = require_admin(request)
    if denied:
        return denied

    data = request.data or {}
    explicit_url = (data.get("proxy_url") or "").strip()

    if explicit_url:
        result = test_proxy_connection(explicit_url)
        return Response(result)

    # Use session to acquire proxy (will launch Tor temporarily if tor_auto is enabled)
    try:
        with acquire_proxy_session() as proxy_url:
            if not proxy_url:
                return Response(
                    {"success": False, "error": "プロキシ設定が無効（OFF）です"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            result = test_proxy_connection(proxy_url)
            return Response(result)
    except Exception as e:
        logger.exception("Proxy test failed: %s", e)
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
