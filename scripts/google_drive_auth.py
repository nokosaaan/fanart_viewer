#!/usr/bin/env python3
"""
One-time Google Drive OAuth setup for the backup/restore feature.

Run this LOCALLY on a machine with a web browser (not on the Pi) — it opens
a browser tab for you to sign in and consent, then prints a refresh token to
paste into `.env` as GOOGLE_DRIVE_REFRESH_TOKEN. The app only ever gets
`drive.file` scope (files it creates itself), not full Drive access.

Setup (once, in Google Cloud Console):
  1. Create/select a project → APIs & Services → Library → enable "Google Drive API".
  2. APIs & Services → Credentials → Create Credentials → OAuth client ID.
     Application type: Desktop app.
  3. Copy the Client ID / Client Secret into `.env` as GOOGLE_DRIVE_CLIENT_ID /
     GOOGLE_DRIVE_CLIENT_SECRET (or pass them as CLI args below).

Usage:
  pip install google-auth-oauthlib google-api-python-client
  python scripts/google_drive_auth.py
"""

import os
import sys
from pathlib import Path

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/drive.file']
ENV_PATH = Path(__file__).resolve().parent.parent / '.env'


def _load_client_creds():
    env = {}
    if dotenv_values and ENV_PATH.exists():
        env = dotenv_values(ENV_PATH)
    client_id = os.environ.get('GOOGLE_DRIVE_CLIENT_ID') or env.get('GOOGLE_DRIVE_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_DRIVE_CLIENT_SECRET') or env.get('GOOGLE_DRIVE_CLIENT_SECRET')
    if not client_id:
        client_id = input('GOOGLE_DRIVE_CLIENT_ID: ').strip()
    if not client_secret:
        client_secret = input('GOOGLE_DRIVE_CLIENT_SECRET: ').strip()
    if not client_id or not client_secret:
        print('client_id/client_secretが必要です', file=sys.stderr)
        sys.exit(1)
    return client_id, client_secret


def main():
    client_id, client_secret = _load_client_creds()
    client_config = {
        'installed': {
            'client_id': client_id,
            'client_secret': client_secret,
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': ['http://localhost'],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    print('\n=== 成功 ===')
    print('.env の GOOGLE_DRIVE_REFRESH_TOKEN に以下を貼り付けてください:\n')
    print(creds.refresh_token)
    print()


if __name__ == '__main__':
    main()
