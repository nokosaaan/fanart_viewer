"""Google Drive backup/restore for the Postgres database.

Auth uses a personal Google account via OAuth2 (refresh token obtained
once, out-of-band, with `scripts/google_drive_auth.py` run on a machine
with a browser). Runtime code only ever refreshes an access token from
that stored refresh token — no interactive consent happens here.

Backups are *data-only* SQL dumps (no CREATE TABLE/DDL) scoped to the item
app's own tables (see BACKUP_TABLES) — not Django's internal auth/contenttype/
session tables, which `migrate` repopulates on its own and would otherwise
collide on restore. Restoring assumes the target database already has an
up-to-date, empty schema (i.e. the app was just deployed and `migrate` has
run, but no data exists yet). This keeps restore_backup() incapable of
clobbering an already-populated database via DDL, matching its intended use:
pulling data onto a freshly set-up host, not overwriting a live one.
"""

import gzip
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive.file']
BACKUP_FOLDER_NAME = 'fanart_viewer_backups'

# googleapiclient's own retry logic (_retry_request) already knows how to
# back off and retry on transient network failures — including ssl.SSLError
# (e.g. the SSLEOFError seen in practice mid-upload on a flaky Pi network
# connection) and socket timeouts/resets — but only if `num_retries` is
# explicitly passed above its default of 0. Every .execute()/.next_chunk()
# call below was previously leaving that at 0, so a single transient error
# during a multi-GB upload/download failed the whole backup/restore instead
# of quietly retrying, as it's designed to.
_NUM_RETRIES = 5
# Upload in smaller chunks than MediaIoBaseUpload's 100MB default so a
# retry after a dropped chunk only has to resend ~10MB, not the whole
# in-flight chunk — matters more the slower/flakier the upload link is.
_UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024


class DriveBackupError(Exception):
    """Raised for any backup/restore failure with a user-facing message."""


class ExistingDataError(DriveBackupError):
    """Raised by restore_backup() when the DB already has data and the
    caller passed mode='strict' (the default). Carries row counts for
    both sides so the caller can show the user a comparison before
    choosing 'overwrite' (wipe first) or 'merge' (append, skipping
    items that already exist -- see _restore_backup_sqlite)."""

    def __init__(self, current: dict, backup: dict):
        self.current = current
        self.backup = backup
        super().__init__('データベースに既存データがあります。内容を比較の上、上書きするか追記するか選択してください。')


def _db_params():
    return {
        'host': os.environ.get('DATABASE_HOST', 'db'),
        'port': os.environ.get('DATABASE_PORT', '5432'),
        'user': os.environ.get('POSTGRES_USER', 'fanart'),
        'password': os.environ.get('POSTGRES_PASSWORD', 'password'),
        'dbname': os.environ.get('POSTGRES_DB', 'fanart'),
    }


def get_drive_service():
    from .drive_creds import get_credentials as _get_drive_credentials

    creds_dict = _get_drive_credentials()
    client_id = creds_dict['client_id']
    client_secret = creds_dict['client_secret']
    refresh_token = creds_dict['refresh_token']
    if not (client_id and client_secret and refresh_token):
        raise DriveBackupError(
            'Google Drive未設定です。設定画面から認証するか、'
            'GOOGLE_DRIVE_CLIENT_ID / GOOGLE_DRIVE_CLIENT_SECRET / '
            'GOOGLE_DRIVE_REFRESH_TOKEN を.envに設定してください'
            '（scripts/google_drive_auth.py参照）。'
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
    ).execute(num_retries=_NUM_RETRIES)
    files = resp.get('files', [])
    if files:
        return files[0]['id']

    folder = service.files().create(
        body={'name': BACKUP_FOLDER_NAME, 'mimeType': 'application/vnd.google-apps.folder'},
        fields='id',
    ).execute(num_retries=_NUM_RETRIES)
    return folder['id']


DUMP_TIMEOUT = 1800  # item_previewimage stores images as bytea and runs several GB; the Pi is slow

# Only the item app's own tables — NOT django_content_type / auth_* / django_session /
# django_admin_log. Those are Django-internal bookkeeping that `migrate` recreates with
# its own rows (e.g. django_content_type id=1) on a freshly migrated target database, so
# dumping them causes primary-key collisions on restore. The app's own IDs don't collide
# because those tables start out empty on a fresh migrate.
BACKUP_TABLES = ['item_charactergroup', 'item_item', 'item_previewimage']


def _is_sqlite() -> bool:
    from django.conf import settings as dj_settings

    return 'sqlite3' in dj_settings.DATABASES['default']['ENGINE']


def _noop_progress(phase, percent):
    pass


def _upload_with_progress(service, file_path, filename, folder_id, mimetype, progress_cb):
    """MediaFileUpload's resumable protocol normally gets driven end-to-end
    by a single .execute() call, which blocks with no visibility into how
    much of the (often many-minutes) upload has actually happened. Driving
    it one chunk at a time via next_chunk() instead exposes exactly that —
    status.progress() is a 0.0-1.0 fraction of the file uploaded so far —
    at the one point in either backup path (SQLite or Postgres) big enough
    for percent-based progress to actually mean something (the DB dump
    /snapshot step itself is comparatively fast; the upload is what a slow
    home/Pi connection makes take a while).
    """
    media = MediaFileUpload(file_path, mimetype=mimetype, resumable=True, chunksize=_UPLOAD_CHUNK_SIZE)
    request = service.files().create(
        body={'name': filename, 'parents': [folder_id]},
        media_body=media,
        fields='id,name,createdTime,size',
    )
    response = None
    while response is None:
        status, response = request.next_chunk(num_retries=_NUM_RETRIES)
        if status is not None:
            progress_cb('uploading', status.progress() * 100)
    progress_cb('uploading', 100)
    return response


def create_backup(progress_cb=None) -> dict:
    """Dispatches to the SQLite-native or Postgres (pg_dump) backup path
    based on which engine is actually configured (see backend.settings'
    DB_ENGINE toggle) -- these are deliberately NOT interchangeable (a
    SQLite backup can only restore into a SQLite deployment and vice
    versa); see _create_backup_sqlite/_restore_backup_sqlite's own
    docstrings for why unifying them isn't worth what it'd cost the
    already-live Postgres deployment's backup size/speed.

    `progress_cb(phase: str, percent: float | None)` — called as the
    backup moves through its phases ('sqlite_backup'/'dumping' ->
    'compressing' -> 'uploading'); `percent` is 0-100 where the phase
    supports it, None where it's genuinely indeterminate (e.g. pg_dump
    itself has no machine-readable progress output). Defaults to a no-op
    so the management-command caller (which has no status view to feed)
    doesn't have to pass one.
    """
    progress_cb = progress_cb or _noop_progress
    if _is_sqlite():
        return _create_backup_sqlite(progress_cb)
    return _create_backup_postgres(progress_cb)


def _create_backup_sqlite(progress_cb) -> dict:
    """Snapshot the SQLite DB file via sqlite3's own online backup API
    (Connection.backup() -- safe to run while the app is live, unlike
    copying the file directly, since it produces a consistent snapshot
    even mid-write) and upload it gzipped.

    Much simpler than the Postgres path: the whole DB already IS a single
    file, so there's no separate dump format/tool to shell out to -- just
    a straight file-level copy. This is exe-only for now (only the exe's
    own launcher.py sets DB_ENGINE=sqlite3); a SQLite backup can only be
    restored into another SQLite deployment (see restore_backup).
    """
    import sqlite3
    from django.conf import settings as dj_settings

    service = get_drive_service()
    folder_id = _get_or_create_backup_folder(service)

    src_path = dj_settings.DATABASES['default']['NAME']
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    filename = f'fanart_backup_sqlite_{timestamp}.sqlite3.gz'

    fd, snapshot_path = tempfile.mkstemp(suffix='.sqlite3')
    os.close(fd)
    os.unlink(snapshot_path)  # Connection.backup() below creates this file itself
    fd2, gz_path = tempfile.mkstemp(suffix='.sqlite3.gz')
    os.close(fd2)
    try:
        src_conn = sqlite3.connect(src_path)
        dst_conn = sqlite3.connect(snapshot_path)
        try:
            # sqlite3.Connection.backup's own `progress(status, remaining,
            # total)` callback, called after each batch of pages copied —
            # `pages=100` (rather than the default -1 = "whole DB in one
            # batch") is what makes it actually fire more than once for a
            # backup fast enough to otherwise finish before it's ever
            # called at all.
            def on_pages_copied(status, remaining, total):
                if total:
                    progress_cb('sqlite_backup', (total - remaining) / total * 100)

            src_conn.backup(dst_conn, pages=100, progress=on_pages_copied)
        finally:
            dst_conn.close()
            src_conn.close()
        progress_cb('sqlite_backup', 100)

        snapshot_size = os.path.getsize(snapshot_path)
        copied = 0
        with open(snapshot_path, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
            while True:
                chunk = f_in.read(4 * 1024 * 1024)
                if not chunk:
                    break
                f_out.write(chunk)
                copied += len(chunk)
                if snapshot_size:
                    progress_cb('compressing', copied / snapshot_size * 100)
        progress_cb('compressing', 100)

        return _upload_with_progress(service, gz_path, filename, folder_id, 'application/gzip', progress_cb)
    finally:
        for p in (snapshot_path, gz_path):
            if os.path.exists(p):
                os.unlink(p)


def _create_backup_postgres(progress_cb) -> dict:
    """Run `pg_dump --data-only`, gzip it, and upload the result to Google Drive.

    Piping pg_dump directly into gzip (rather than writing the plain dump to
    disk first) avoids ever needing the full uncompressed size (several GB,
    since bytea columns roughly double in the text dump format) as free disk
    space on the Pi.

    Returns the created Drive file's metadata (id, name, createdTime, size).
    """
    service = get_drive_service()
    folder_id = _get_or_create_backup_folder(service)

    params = _db_params()
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    filename = f'fanart_backup_{timestamp}.sql.gz'

    fd, dump_path = tempfile.mkstemp(suffix='.sql.gz')
    os.close(fd)
    try:
        # pg_dump has no machine-readable progress output of its own, so
        # this phase's percent stays indeterminate (None) — only the
        # (comparatively slow, on a Pi's home connection) upload below
        # gets a real percentage.
        progress_cb('dumping', None)
        env = {**os.environ, 'PGPASSWORD': params['password']}
        with open(dump_path, 'wb') as out_f:
            pg_proc = subprocess.Popen(
                [
                    'pg_dump',
                    '-h', params['host'],
                    '-p', str(params['port']),
                    '-U', params['user'],
                    '-d', params['dbname'],
                    '--data-only',
                    '--no-owner',
                    '--format=plain',
                    *[f'--table={t}' for t in BACKUP_TABLES],
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            gzip_proc = subprocess.Popen(['gzip', '-c'], stdin=pg_proc.stdout, stdout=out_f, stderr=subprocess.PIPE)
            pg_proc.stdout.close()
            try:
                _, gzip_err = gzip_proc.communicate(timeout=DUMP_TIMEOUT)
            except subprocess.TimeoutExpired:
                pg_proc.kill()
                gzip_proc.kill()
                raise DriveBackupError(f'pg_dumpがタイムアウトしました({DUMP_TIMEOUT}秒)')
            _, pg_err = pg_proc.communicate()

        if pg_proc.returncode != 0:
            raise DriveBackupError(f'pg_dump失敗: {pg_err.decode(errors="replace").strip()[:500]}')
        if gzip_proc.returncode != 0:
            raise DriveBackupError(f'gzip失敗: {gzip_err.decode(errors="replace").strip()[:500]}')

        return _upload_with_progress(service, dump_path, filename, folder_id, 'application/gzip', progress_cb)
    finally:
        os.unlink(dump_path)


_COPY_RE = re.compile(r'^COPY public\.(\w+) \(')


def _count_dump_rows(dump_path: str, is_gz: bool) -> dict:
    """Count data rows per table in a data-only SQL dump by scanning its
    COPY ... FROM stdin; ... \\. blocks, without touching the database.

    Streams the (possibly gzipped) file line by line — cheap enough even
    for the several-GB dumps this app produces — so it's safe to call just
    to preview a restore before committing to it.
    """
    opener = gzip.open if is_gz else open
    counts = {}
    current_table = None
    with opener(dump_path, 'rt', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if current_table is None:
                m = _COPY_RE.match(line)
                if m and m.group(1) in BACKUP_TABLES:
                    current_table = m.group(1)
                    counts[current_table] = 0
            elif line.rstrip('\n') == '\\.':
                current_table = None
            else:
                counts[current_table] += 1
    return counts


def list_backups() -> list:
    service = get_drive_service()
    folder_id = _get_or_create_backup_folder(service)
    resp = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields='files(id,name,createdTime,size)',
        orderBy='createdTime desc',
        pageSize=50,
    ).execute(num_retries=_NUM_RETRIES)
    return resp.get('files', [])


def get_backup_folder_url() -> str:
    """Direct link to the Drive folder backups are stored in, for the
    settings panel to link out to (previously there was no way to reach
    it from the app itself — you had to already know to search Drive for
    BACKUP_FOLDER_NAME)."""
    service = get_drive_service()
    folder_id = _get_or_create_backup_folder(service)
    return f'https://drive.google.com/drive/folders/{folder_id}'


def restore_backup(file_id: str, mode: str = 'strict', progress_cb=None) -> None:
    """Download the given Drive backup and load it into the database --
    dispatches the same way create_backup does. A SQLite backup can only
    be restored into a SQLite deployment (and a Postgres one only into
    Postgres); there's no cross-engine restore path (see create_backup's
    docstring for why).

    `progress_cb(phase, percent)` — same contract as create_backup's:
    real percent (via MediaIoBaseDownload's own per-chunk status) while
    downloading the backup file from Drive (the phase most likely to
    actually take a while on a slow home connection), phase='importing'
    with percent=None for the DB-side load itself (a single SQL statement
    for 'strict'/'overwrite', or the row-by-row merge — see
    _merge_backup_sqlite — for 'merge'; none of those expose a meaningful
    fine-grained progress signal of their own without much more invasive
    changes to that already-intricate function, so this stays honest
    about not having one rather than faking a percentage). Defaults to a
    no-op so callers with no status view to feed (none currently) don't
    have to pass one.

    `mode`:
      'strict'    (default) -- raise ExistingDataError if the DB already
                  has data; otherwise just insert (identical to restoring
                  into a fresh, empty DB).
      'overwrite' -- delete all existing rows first, then insert the
                  backup's rows as-is (same ids as the backup).
      'merge'     -- keep existing data, append the backup's rows with
                  FRESH ids (never reusing the backup's own row ids, to
                  avoid colliding with whatever this DB already assigned
                  those same id numbers to), skipping any Item that
                  already exists locally under the same (external_id,
                  source) pair -- see _restore_backup_sqlite. SQLite only;
                  Postgres restore doesn't support this mode (see
                  _restore_backup_postgres).
    """
    progress_cb = progress_cb or _noop_progress
    if _is_sqlite():
        return _restore_backup_sqlite(file_id, mode=mode, progress_cb=progress_cb)
    return _restore_backup_postgres(file_id, mode=mode, progress_cb=progress_cb)


def _restore_backup_sqlite(file_id: str, mode: str = 'strict', progress_cb=None) -> None:
    """Download a SQLite snapshot backup and copy this app's own tables
    from it into the live DB via SQLite's ATTACH DATABASE (attaching the
    downloaded file as a second, temporary database on the SAME
    connection Django itself uses, so it shares Django's configured
    timeout/locking behavior rather than racing it with an unrelated
    fresh connection).

    See restore_backup's docstring for what each `mode` does. 'strict'
    and 'overwrite' both copy rows via a single SQL `INSERT ... SELECT *`
    (identical ids to the backup -- safe only because either the DB is
    confirmed empty first, or it was just wiped). 'merge' is row-by-row
    through the ORM instead (_merge_backup_sqlite), since it has to
    assign fresh ids and remap foreign keys as it goes.
    """
    import shutil
    import sqlite3

    from django.db import connection, transaction

    from .models import Item, CharacterGroup, PreviewImage

    progress_cb = progress_cb or _noop_progress
    has_existing = Item.objects.exists() or CharacterGroup.objects.exists()

    service = get_drive_service()

    fd, download_path = tempfile.mkstemp(suffix='.download')
    fd2, plain_path = tempfile.mkstemp(suffix='.sqlite3')
    os.close(fd2)
    os.unlink(plain_path)
    try:
        request = service.files().get_media(fileId=file_id)
        with os.fdopen(fd, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk(num_retries=_NUM_RETRIES)
                if status is not None:
                    progress_cb('downloading', status.progress() * 100)
        progress_cb('downloading', 100)

        with open(download_path, 'rb') as fh:
            is_gz = fh.read(2) == b'\x1f\x8b'  # gzip magic number; trust bytes over the filename

        progress_cb('importing', None)
        if is_gz:
            with gzip.open(download_path, 'rb') as f_in, open(plain_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copyfile(download_path, plain_path)

        # Read-only peek for row counts, entirely separate from the live
        # connection -- doesn't touch the app's DB at all yet.
        backup_conn = sqlite3.connect(f'file:{plain_path}?mode=ro', uri=True)
        try:
            backup_counts = {
                table: backup_conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in BACKUP_TABLES
            }
        finally:
            backup_conn.close()

        if has_existing and mode == 'strict':
            raise ExistingDataError(
                current={
                    'item_charactergroup': CharacterGroup.objects.count(),
                    'item_item': Item.objects.count(),
                    'item_previewimage': PreviewImage.objects.count(),
                },
                backup=backup_counts,
            )

        with connection.cursor() as attach_cursor:
            attach_cursor.execute('ATTACH DATABASE %s AS backup_src', [plain_path])
        try:
            if mode == 'merge':
                with transaction.atomic():
                    result = _merge_backup_sqlite(connection)
            else:
                try:
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            if has_existing and mode == 'overwrite':
                                # item_previewimage FKs to item_item -- delete it first.
                                cursor.execute('DELETE FROM item_previewimage')
                                cursor.execute('DELETE FROM item_item')
                                cursor.execute('DELETE FROM item_charactergroup')
                            cursor.execute('INSERT INTO item_charactergroup SELECT * FROM backup_src.item_charactergroup')
                            cursor.execute('INSERT INTO item_item SELECT * FROM backup_src.item_item')
                            cursor.execute('INSERT INTO item_previewimage SELECT * FROM backup_src.item_previewimage')
                except sqlite3.OperationalError as e:
                    raise DriveBackupError(f'復元失敗: {e}') from e
                result = None
        finally:
            with connection.cursor() as detach_cursor:
                detach_cursor.execute('DETACH DATABASE backup_src')
        return result
    finally:
        for p in (download_path, plain_path):
            if os.path.exists(p):
                os.unlink(p)


def _concrete_columns(model, exclude_pk=True):
    """Column names for a model's own concrete fields, in the model's own
    field order -- used to read/write rows generically (by name) instead
    of relying on a raw `SELECT *` column order, which is fragile across
    schema/migration changes between when a backup was made and when
    it's restored."""
    return [
        f.column for f in model._meta.concrete_fields
        if not (exclude_pk and f.primary_key)
    ]


def _quoted(cols):
    """Double-quote column names for use in raw SQL -- PreviewImage.order
    maps to a column literally named `order`, a SQL reserved word that
    breaks an unquoted SELECT/comma list (confirmed live:
    `sqlite3.OperationalError: near "order": syntax error`)."""
    return ', '.join(f'"{c}"' for c in cols)


def _as_aware_utc(raw_value):
    """Normalize a DateTimeField's raw fetched value into a tz-aware UTC
    datetime. Python's sqlite3 module auto-converts a "datetime"-declared
    column straight into a `datetime.datetime` object on fetch (via its
    registered converter, matched by declared column type regardless of
    which ATTACHed database the row came from) -- but confirmed live that
    it comes back NAIVE either way (whether sqlite3 hands back a
    datetime object directly, or -- if that conversion ever isn't active
    -- plain text needing datetime.fromisoformat/parse_datetime first).
    Django's sqlite backend always stores UTC-normalized datetimes when
    USE_TZ=True (regardless of the configured TIME_ZONE), so reattaching
    plain UTC tzinfo to a naive result is the correct inverse, not a
    guess. Passing a still-naive value straight to the ORM instead would
    go through DateTimeField.get_prep_value's own naive-datetime
    handling, which assumes the CURRENT default timezone rather than UTC
    and would silently shift the value by that offset on any server not
    itself configured for UTC -- confirmed live via Django's own
    RuntimeWarning ("received a naive datetime ... while time zone
    support is active") the first time this was tried without this
    conversion.
    """
    if raw_value is None:
        return None
    from datetime import datetime as dt_class
    from datetime import timezone as dt_timezone

    from django.utils.dateparse import parse_datetime

    dt = raw_value if isinstance(raw_value, dt_class) else parse_datetime(raw_value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def _merge_backup_sqlite(connection) -> dict:
    """Append an ATTACHed backup's rows into the live DB WITHOUT deleting
    anything already there -- for combining archives built up
    independently on two different devices/installs, rather than
    replacing one with the other (see restore_backup's mode='merge').

    Must run with `backup_src` already ATTACHed and inside the caller's
    own transaction.atomic() block (see _restore_backup_sqlite).

    Row ids are never copied as-is from the backup -- both DBs assign
    their own ids independently, so the SAME id number in each almost
    certainly refers to two unrelated rows. Every inserted row gets a
    fresh id from this DB's own autoincrement sequence, and every
    foreign key (PreviewImage.item, CharacterGroup.parent) is remapped
    from the backup's old id to whichever id the corresponding row
    actually ended up with here.

    Dedup rules (skip re-adding something that's already effectively
    present, rather than ever risk losing local data):
      - CharacterGroup: matched by `name` (the model's own unique
        constraint) -- an existing local group with the same name is
        left untouched; only groups with a genuinely new name are
        inserted.
      - Item: matched by (external_id, source) -- the same convention
        already used elsewhere in this app (see poll_twitter_updates.py/
        poll_pixiv_bookmarks.py's own known_ids) to mean "the same real
        post". A manually-registered item (source='manual', external_id
        a millisecond timestamp) has a small chance of coincidentally
        colliding with another manual item from the other device, but
        this mirrors how manual items are already deduplicated
        everywhere else in the app.
      - PreviewImage: always follows its own Item -- skipped whenever
        that Item was itself skipped as a duplicate (its images are
        presumably already present locally too), inserted with the
        remapped item id otherwise.

    Returns a small summary dict (counts of what was actually added vs.
    skipped) for the caller to report back to the user.
    """
    from .models import CharacterGroup, Item, PreviewImage

    with connection.cursor() as cursor:
        # --- CharacterGroup: dedup by name, remap self-referential parent ---
        cg_cols = _concrete_columns(CharacterGroup)  # e.g. ['name','characters','titles','parent_id','created_at']
        cursor.execute(f'SELECT "id", {_quoted(cg_cols)} FROM backup_src.item_charactergroup')
        backup_groups = cursor.fetchall()

        group_id_map = {}  # old (backup) id -> new (local) id
        pending_parent = {}  # new local id -> old (backup) parent id, only for freshly-inserted groups
        groups_added = 0
        for row in backup_groups:
            old_id = row[0]
            kwargs = dict(zip(cg_cols, row[1:]))
            old_parent_id = kwargs.pop('parent_id', None)
            for json_col in ('characters', 'titles'):
                if kwargs.get(json_col) is not None:
                    kwargs[json_col] = json.loads(kwargs[json_col])

            existing = CharacterGroup.objects.filter(name=kwargs['name']).first()
            if existing is not None:
                group_id_map[old_id] = existing.id
                continue

            # created_at has auto_now_add=True, so .create() always stamps
            # "now" regardless of what's passed -- restore the backup's
            # original timestamp afterwards via .update(), which (unlike
            # .create()/.save()) does NOT re-apply auto_now_add.
            original_created_at = _as_aware_utc(kwargs.pop('created_at', None))
            new_group = CharacterGroup.objects.create(parent=None, **kwargs)
            if original_created_at is not None:
                CharacterGroup.objects.filter(pk=new_group.pk).update(created_at=original_created_at)
            group_id_map[old_id] = new_group.id
            groups_added += 1
            if old_parent_id is not None:
                pending_parent[new_group.id] = old_parent_id

        for new_id, old_parent_id in pending_parent.items():
            mapped_parent_id = group_id_map.get(old_parent_id)
            if mapped_parent_id is not None:
                CharacterGroup.objects.filter(id=new_id).update(parent_id=mapped_parent_id)

        # --- Item: dedup by (external_id, source) ---
        item_cols = _concrete_columns(Item)
        item_json_cols = {f.column for f in Item._meta.concrete_fields if f.get_internal_type() == 'JSONField'}
        cursor.execute(f'SELECT "id", {_quoted(item_cols)} FROM backup_src.item_item')
        backup_items = cursor.fetchall()

        item_id_map = {}  # old (backup) id -> new (local) id; absent = skipped as a duplicate
        items_added = 0
        items_skipped = 0
        for row in backup_items:
            old_id = row[0]
            kwargs = dict(zip(item_cols, row[1:]))
            for json_col in item_json_cols:
                if kwargs.get(json_col) is not None:
                    kwargs[json_col] = json.loads(kwargs[json_col])

            existing = Item.objects.filter(external_id=kwargs['external_id'], source=kwargs['source']).first()
            if existing is not None:
                items_skipped += 1
                continue

            # description_checked_at is a plain (non-auto) DateTimeField --
            # no auto_now_add issue, but still needs the same aware-UTC
            # conversion as created_at below (see _as_aware_utc).
            if kwargs.get('description_checked_at') is not None:
                kwargs['description_checked_at'] = _as_aware_utc(kwargs['description_checked_at'])

            # Same auto_now_add caveat as CharacterGroup.created_at above.
            original_created_at = _as_aware_utc(kwargs.pop('created_at', None))
            new_item = Item.objects.create(**kwargs)
            if original_created_at is not None:
                Item.objects.filter(pk=new_item.pk).update(created_at=original_created_at)
            item_id_map[old_id] = new_item.id
            items_added += 1

        # --- PreviewImage: always follows its Item ---
        pi_cols = _concrete_columns(PreviewImage)  # ['item_id', 'order', 'data', 'content_type']
        cursor.execute(f'SELECT {_quoted(pi_cols)} FROM backup_src.item_previewimage')
        backup_previews = cursor.fetchall()

        previews_added = 0
        for row in backup_previews:
            kwargs = dict(zip(pi_cols, row))
            old_item_id = kwargs.pop('item_id')
            new_item_id = item_id_map.get(old_item_id)
            if new_item_id is None:
                continue  # this image's Item was skipped as a duplicate
            PreviewImage.objects.create(item_id=new_item_id, **kwargs)
            previews_added += 1

    return {
        'groups_added': groups_added,
        'items_added': items_added,
        'items_skipped': items_skipped,
        'previews_added': previews_added,
    }


def _restore_backup_postgres(file_id: str, mode: str = 'strict', progress_cb=None) -> None:
    """Download the given Drive backup and load it into the database.

    If the database already has data and `mode` is 'strict' (the
    default), raises ExistingDataError with row counts for both the
    current DB and the backup instead of touching anything -- the caller
    is expected to show the user that comparison and re-call with
    mode='overwrite' to proceed. When mode='overwrite', existing rows in
    the app's own tables are cleared first (TRUNCATE ... CASCADE) so the
    backup's rows can be loaded without primary-key collisions.

    mode='merge' (append without deleting, dedup by matching key, see
    _merge_backup_sqlite) is NOT implemented here -- a psql dump restore
    loads via COPY/INSERT statements that carry the backup's own row ids
    verbatim, which merge mode can't allow (see restore_backup's
    docstring for why). The exe-packaged distribution is SQLite-only
    already (this Postgres path only serves the docker deployment), so
    there's no user-facing path that would ever need this combination in
    practice -- surfacing a clear error is enough.
    """
    if mode == 'merge':
        raise DriveBackupError('Postgresデプロイでは追記(マージ)復元はサポートしていません。上書きのみ対応しています。')

    from .models import Item, CharacterGroup, PreviewImage

    progress_cb = progress_cb or _noop_progress
    has_existing = Item.objects.exists() or CharacterGroup.objects.exists()

    service = get_drive_service()
    params = _db_params()

    fd, dump_path = tempfile.mkstemp(suffix='.download')
    try:
        request = service.files().get_media(fileId=file_id)
        with os.fdopen(fd, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk(num_retries=_NUM_RETRIES)
                if status is not None:
                    progress_cb('downloading', status.progress() * 100)
        progress_cb('downloading', 100)

        with open(dump_path, 'rb') as fh:
            is_gz = fh.read(2) == b'\x1f\x8b'  # gzip magic number; trust bytes over the filename

        if has_existing and mode == 'strict':
            raise ExistingDataError(
                current={
                    'item_charactergroup': CharacterGroup.objects.count(),
                    'item_item': Item.objects.count(),
                    'item_previewimage': PreviewImage.objects.count(),
                },
                backup=_count_dump_rows(dump_path, is_gz),
            )

        progress_cb('importing', None)
        env = {**os.environ, 'PGPASSWORD': params['password']}
        psql_cmd = [
            'psql',
            '-h', params['host'],
            '-p', str(params['port']),
            '-U', params['user'],
            '-d', params['dbname'],
            '-v', 'ON_ERROR_STOP=1',
        ]

        if has_existing and mode == 'overwrite':
            # item_previewimage FKs to item_item, so CASCADE covers it too.
            truncate = subprocess.run(
                psql_cmd + ['-c', 'TRUNCATE item_previewimage, item_item, item_charactergroup RESTART IDENTITY CASCADE;'],
                env=env, capture_output=True, timeout=60,
            )
            if truncate.returncode != 0:
                stderr = truncate.stderr.decode('utf-8', errors='replace').strip()
                raise DriveBackupError(f'既存データの削除に失敗: {stderr[:500]}')

        if is_gz:
            gunzip_proc = subprocess.Popen(['gunzip', '-c', dump_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            result = subprocess.run(psql_cmd, env=env, stdin=gunzip_proc.stdout, capture_output=True, timeout=DUMP_TIMEOUT)
            gunzip_proc.stdout.close()
            gunzip_proc.wait()
        else:
            with open(dump_path, 'rb') as fh:
                result = subprocess.run(psql_cmd, env=env, stdin=fh, capture_output=True, timeout=DUMP_TIMEOUT)
        if result.returncode != 0:
            # decode leniently: psql/pg error text isn't guaranteed to be valid UTF-8
            # (e.g. it can echo back raw bytes from malformed input), and text=True
            # would crash on that instead of surfacing the actual error.
            stderr = result.stderr.decode('utf-8', errors='replace').strip()
            raise DriveBackupError(f'復元失敗: {stderr[:500]}')
    finally:
        os.unlink(dump_path)
