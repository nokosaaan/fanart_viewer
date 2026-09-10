"""One-time production migration: copies all `item` app data from the
currently-configured database (must be PostgreSQL — run this BEFORE
switching DB_ENGINE to sqlite3 in .env) into a fresh SQLite file.

Why not `dumpdata`/`loaddata`: item_previewimage alone holds several GB of
BinaryField data on real deployments (see docker-compose.prod.yml's own
GUNICORN_TIMEOUT comment) — Django's JSON fixture serializer base64-encodes
BinaryField (~33% larger) and loaddata materializes the whole fixture in
memory before saving any of it, which risks exhausting RAM on Raspberry Pi
hardware. This streams rows from Postgres in bounded chunks
(.iterator(chunk_size=...)) and bulk_creates them into SQLite in the same
bounded chunks, so peak memory stays around chunk_size rows at a time
regardless of total table size.

Registers a second Django DB alias ('sqlite_target') at runtime pointing at
the destination file, rather than requiring a second settings module or
process — a well-established pattern for one-off cross-database scripts.
FK constraints are disabled for the duration of the copy (PRAGMA
foreign_keys=OFF) so per-model insert order doesn't matter; explicit PK
values on the copied objects are preserved (bulk_create inserts the given
pk rather than auto-assigning one when it's already set), so cross-model
FK relationships stay intact by id, and SQLite's own next-autoincrement-id
continues correctly from max(id)+1 afterward with no manual reset needed.

See RELEASE_LOCAL.md's "SQLiteへの移行" section for the full procedure
(back up first, run this, then flip DB_ENGINE, then restart).
"""
import os
import time

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = (
        "Copy all item-app data from the current (PostgreSQL) database into a new "
        "SQLite file, for switching DB_ENGINE=sqlite3. Run this BEFORE flipping that "
        "setting — it reads from 'default' (must still be postgres) and writes to "
        "the destination file given as an argument."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'sqlite_path',
            help='Destination .sqlite3 file to create (e.g. /app/data/db.sqlite3 — '
                 'the default SQLITE_PATH the app itself will look for once DB_ENGINE=sqlite3).',
        )
        parser.add_argument(
            '--chunk-size', type=int, default=100,
            help='Rows copied per batch (default 100). Lower this if the process runs '
                 'out of memory on models with large BinaryField data (Item.preview_data, '
                 'PreviewImage.data).',
        )
        parser.add_argument(
            '--overwrite', action='store_true',
            help='Delete an existing file at sqlite_path first instead of erroring out.',
        )

    def handle(self, *args, **options):
        sqlite_path = options['sqlite_path']
        chunk_size = options['chunk_size']

        if connections['default'].vendor != 'postgresql':
            raise CommandError(
                f"The current 'default' database is {connections['default'].vendor!r}, not "
                "postgresql. Run this command BEFORE setting DB_ENGINE=sqlite3 — it copies "
                "FROM the currently-configured database TO the new file."
            )

        if os.path.exists(sqlite_path):
            if options['overwrite']:
                os.remove(sqlite_path)
            else:
                raise CommandError(f'{sqlite_path} already exists. Pass --overwrite to replace it.')

        connections.databases['sqlite_target'] = {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': sqlite_path,
            'ATOMIC_REQUESTS': False,
            'AUTOCOMMIT': True,
            'CONN_MAX_AGE': 0,
            'CONN_HEALTH_CHECKS': False,
            'OPTIONS': {},
            'TIME_ZONE': None,
        }

        self.stdout.write('Creating schema on the new SQLite file (running migrations)...')
        call_command('migrate', database='sqlite_target', verbosity=1, interactive=False)

        target_conn = connections['sqlite_target']
        with target_conn.cursor() as cur:
            cur.execute('PRAGMA foreign_keys=OFF;')

        # `item` app models only — Django's own auth/contenttypes/sessions
        # tables were just repopulated by `migrate` itself above, not user
        # data (matches drive_backup.py's own BACKUP_TABLES scoping).
        item_models = apps.get_app_config('item').get_models()

        mismatches = []
        for model in item_models:
            source_qs = model.objects.using('default').order_by('pk')
            total = source_qs.count()
            if total == 0:
                self.stdout.write(f'{model.__name__}: 0 rows, skipping')
                continue

            self.stdout.write(f'{model.__name__}: copying {total} row(s)...')
            done = 0
            batch = []
            t0 = time.time()
            for obj in source_qs.iterator(chunk_size=chunk_size):
                batch.append(obj)
                if len(batch) >= chunk_size:
                    model.objects.using('sqlite_target').bulk_create(batch, batch_size=chunk_size)
                    done += len(batch)
                    batch = []
                    self.stdout.write(f'  {done}/{total} ({time.time() - t0:.0f}s elapsed)')
            if batch:
                model.objects.using('sqlite_target').bulk_create(batch, batch_size=chunk_size)
                done += len(batch)

            dest_count = model.objects.using('sqlite_target').count()
            if dest_count != total:
                mismatches.append((model.__name__, total, dest_count))
                self.stdout.write(self.style.ERROR(
                    f'{model.__name__}: MISMATCH — source had {total}, destination has {dest_count}'
                ))
            else:
                self.stdout.write(self.style.SUCCESS(f'{model.__name__}: {done}/{total} copied, counts match'))

        with target_conn.cursor() as cur:
            cur.execute('PRAGMA foreign_keys=ON;')
        target_conn.close()

        if mismatches:
            raise CommandError(
                f'Row count mismatches in {len(mismatches)} model(s): {mismatches}. '
                f'{sqlite_path} was left in place for inspection — do NOT switch DB_ENGINE '
                f'to it yet.'
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {sqlite_path} now holds a full, verified copy of the item app data.\n"
            f"Next: set DB_ENGINE=sqlite3 in .env (SQLITE_PATH only if not using this exact "
            f"path), then restart the stack."
        ))
