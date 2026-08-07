"""Google Drive backup/restore for the Postgres database.

Auth uses a personal Google account via OAuth2 (refresh token obtained
once, out-of-band, with `scripts/google_drive_auth.py` run on a machine
with a browser). Runtime code only ever refreshes an access token from
that stored refresh token — no interactive consent happens here.

Backups are *data-only* SQL dumps (no CREATE TABLE/DDL): restoring assumes
the target database already has an up-to-date, empty schema (i.e. the app
was just deployed and `migrate` has run, but no data exists yet). This
keeps restore_backup() incapable of clobbering an already-populated
database via DDL, matching its intended use: pulling data onto a freshly
set-up host, not overwriting a live one.
"""

import os
import subprocess
import tempfile
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive.file']
BACKUP_FOLDER_NAME = 'fanart_viewer_backups'


class DriveBackupError(Exception):
    """Raised for any backup/restore failure with a user-facing message."""


def _db_params():
    return {
        'host': os.environ.get('DATABASE_HOST', 'db'),
        'port': os.environ.get('DATABASE_PORT', '5432'),
        'user': os.environ.get('POSTGRES_USER', 'fanart'),
        'password': os.environ.get('POSTGRES_PASSWORD', 'password'),
        'dbname': os.environ.get('POSTGRES_DB', 'fanart'),
    }


def get_drive_service():
    client_id = os.environ.get('GOOGLE_DRIVE_CLIENT_ID', '')
    client_secret = os.environ.get('GOOGLE_DRIVE_CLIENT_SECRET', '')
    refresh_token = os.environ.get('GOOGLE_DRIVE_REFRESH_TOKEN', '')
    if not (client_id and client_secret and refresh_token):
        raise DriveBackupError(
            'Google Drive未設定です。GOOGLE_DRIVE_CLIENT_ID / '
            'GOOGLE_DRIVE_CLIENT_SECRET / GOOGLE_DRIVE_REFRESH_TOKEN を'
            '.envに設定してください（scripts/google_drive_auth.py参照）。'
        )
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=client_id,
        client_secret=client_secret,
        scopes=DRIVE_SCOPES,
    )
    creds.refresh(Request())
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


def _get_or_create_backup_folder(service):
    resp = service.files().list(
        q=f"name='{BACKUP_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields='files(id,name)',
        spaces='drive',
    ).execute()
    files = resp.get('files', [])
    if files:
        return files[0]['id']

    folder = service.files().create(
        body={'name': BACKUP_FOLDER_NAME, 'mimeType': 'application/vnd.google-apps.folder'},
        fields='id',
    ).execute()
    return folder['id']


def create_backup() -> dict:
    """Run `pg_dump --data-only` and upload the result to Google Drive.

    Returns the created Drive file's metadata (id, name, createdTime, size).
    """
    service = get_drive_service()
    folder_id = _get_or_create_backup_folder(service)

    params = _db_params()
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    filename = f'fanart_backup_{timestamp}.sql'

    fd, dump_path = tempfile.mkstemp(suffix='.sql')
    os.close(fd)
    try:
        env = {**os.environ, 'PGPASSWORD': params['password']}
        result = subprocess.run(
            [
                'pg_dump',
                '-h', params['host'],
                '-p', str(params['port']),
                '-U', params['user'],
                '-d', params['dbname'],
                '--data-only',
                '--no-owner',
                '--format=plain',
                '-f', dump_path,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise DriveBackupError(f'pg_dump失敗: {result.stderr.strip()[:500]}')

        media = MediaFileUpload(dump_path, mimetype='application/sql', resumable=False)
        uploaded = service.files().create(
            body={'name': filename, 'parents': [folder_id]},
            media_body=media,
            fields='id,name,createdTime,size',
        ).execute()
        return uploaded
    finally:
        os.unlink(dump_path)


def list_backups() -> list:
    service = get_drive_service()
    folder_id = _get_or_create_backup_folder(service)
    resp = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields='files(id,name,createdTime,size)',
        orderBy='createdTime desc',
        pageSize=50,
    ).execute()
    return resp.get('files', [])


def restore_backup(file_id: str) -> None:
    """Download the given Drive backup and load it into the database.

    Raises DriveBackupError if the database already has data — restore is
    only intended for populating a freshly migrated, empty database on a
    new host.
    """
    from .models import Item, CharacterGroup

    if Item.objects.exists() or CharacterGroup.objects.exists():
        raise DriveBackupError(
            'データベースに既存データがあるため復元を中止しました。'
            'この機能はデバイス移行時の空DBへの初回投入専用です。'
        )

    service = get_drive_service()
    params = _db_params()

    fd, dump_path = tempfile.mkstemp(suffix='.sql')
    try:
        request = service.files().get_media(fileId=file_id)
        with os.fdopen(fd, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        env = {**os.environ, 'PGPASSWORD': params['password']}
        with open(dump_path, 'r') as fh:
            result = subprocess.run(
                [
                    'psql',
                    '-h', params['host'],
                    '-p', str(params['port']),
                    '-U', params['user'],
                    '-d', params['dbname'],
                    '-v', 'ON_ERROR_STOP=1',
                ],
                env=env,
                stdin=fh,
                capture_output=True,
                text=True,
                timeout=300,
            )
        if result.returncode != 0:
            raise DriveBackupError(f'復元失敗: {result.stderr.strip()[:500]}')
    finally:
        os.unlink(dump_path)
