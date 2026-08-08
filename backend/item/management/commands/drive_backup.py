"""Run Google Drive backup/restore directly, bypassing the HTTP/Cloudflare path.

The web UI's backup button goes through the Cloudflare Tunnel, which has its
own edge timeout (~100s) independent of DUMP_TIMEOUT in drive_backup.py — a
large `item_previewimage` table (full-res images stored as bytea) can easily
take longer than that to pg_dump + upload, so the browser gets a 524 "A
Timeout Occurred" even though the backup may still be running server-side.
Running this command via `docker compose exec web ...` avoids the tunnel
entirely, so it can run as long as it actually needs.

Usage:
  docker compose -f docker-compose.prod.yml exec web python manage.py drive_backup create
  docker compose -f docker-compose.prod.yml exec web python manage.py drive_backup list
  docker compose -f docker-compose.prod.yml exec web python manage.py drive_backup restore --file-id <id> [--overwrite]
"""

from django.core.management.base import BaseCommand, CommandError

from item.drive_backup import create_backup, list_backups, restore_backup, DriveBackupError, ExistingDataError


class Command(BaseCommand):
    help = 'Create/list/restore Google Drive database backups without going through the Cloudflare Tunnel.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['create', 'list', 'restore'])
        parser.add_argument('--file-id', help='Drive file ID to restore (required for `restore`)')
        parser.add_argument('--overwrite', action='store_true', help='Truncate existing data before restoring')

    def handle(self, *args, **options):
        action = options['action']

        if action == 'create':
            self.stdout.write('Running pg_dump and uploading to Google Drive (this can take a while for a large DB)...')
            try:
                meta = create_backup()
            except DriveBackupError as e:
                raise CommandError(str(e))
            self.stdout.write(self.style.SUCCESS(f"Uploaded: {meta['name']} (id={meta['id']}, size={meta.get('size', '?')} bytes)"))

        elif action == 'list':
            try:
                files = list_backups()
            except DriveBackupError as e:
                raise CommandError(str(e))
            if not files:
                self.stdout.write('No backups found.')
            for f in files:
                self.stdout.write(f"{f['id']}  {f.get('createdTime', '?')}  {f.get('size', '?'):>12} bytes  {f['name']}")

        elif action == 'restore':
            file_id = options.get('file_id')
            if not file_id:
                raise CommandError('--file-id is required for restore')
            overwrite = options.get('overwrite', False)
            self.stdout.write(f"Restoring from {file_id} (overwrite={overwrite})...")
            try:
                restore_backup(file_id, overwrite=overwrite)
            except ExistingDataError as e:
                self.stdout.write(self.style.WARNING('Database already has data:'))
                self.stdout.write(f'  current: {e.current}')
                self.stdout.write(f'  backup:  {e.backup}')
                raise CommandError('Re-run with --overwrite to replace existing data with the backup.')
            except DriveBackupError as e:
                raise CommandError(str(e))
            self.stdout.write(self.style.SUCCESS('Restore complete.'))
