"""Outbound notifications for background jobs that have no interactive user
to report to (see item.management.commands.poll_twitter_updates). Currently
just a Discord incoming webhook — the only channel wired up so far.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)


def notify_discord(message: str) -> None:
    """POST a plain-text message to NOTIFY_DISCORD_WEBHOOK_URL, if set.

    Silently does nothing when the env var is empty, so this is safe to call
    unconditionally from any background job — deployments that don't care
    about notifications just never set the webhook URL.
    """
    url = os.environ.get('NOTIFY_DISCORD_WEBHOOK_URL', '').strip()
    if not url:
        return
    try:
        requests.post(url, json={'content': message}, timeout=10)
    except requests.RequestException:
        logger.exception('Failed to send Discord notification')
