import json
import glob
import os

from django.core.management.base import BaseCommand
from item.models import Item


def normalize_entry(key, data):
    # key is string number
    external_id = int(key)
    return {
        'external_id': external_id,
        'situation': data.get('SITUATION', '') or '',
        'titles': data.get('TITLE', []),
        'characters': data.get('CHARACTER', []),
        'artist': data.get('ARTIST', '') or '',
        'link': data.get('LINK', '') or '',
        'tags': data.get('TAG', None),
    }


class Command(BaseCommand):
    help = 'Import JSON files from backend/data into DB (idempotent)'
    def handle(self, *args, **options):
        # look for possible data directories and collect unique files (dedupe by realpath)
        candidates = []
        base_dirs = [
            '/app/data',
            os.path.join(os.getcwd(), 'data'),
            os.path.join(os.getcwd(), 'backend', 'data'),
        ]
        for d in base_dirs:
            if os.path.isdir(d):
                candidates.extend(glob.glob(os.path.join(d, '*.json')))

        # dedupe by resolved absolute path to avoid processing same file multiple times
        resolved = []
        seen = set()
        for p in candidates:
            try:
                rp = os.path.realpath(p)
            except Exception:
                rp = os.path.abspath(p)
            if rp not in seen:
                seen.add(rp)
                resolved.append(p)

        if not resolved:
            self.stdout.write(self.style.WARNING('No JSON files found in expected data directories.'))
            return

        total_created = total_updated = total_errors = 0
        for path in resolved:
            self.stdout.write(f'Processing {path}...')
            # derive source from filename (basename without extension)
            src = os.path.splitext(os.path.basename(path))[0]
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Failed to read {path}: {e}'))
                total_errors += 1
                continue

            if not isinstance(data, dict):
                self.stdout.write(self.style.WARNING(f'Skipped {path}: unexpected JSON root type'))
                continue

            created = updated = errors = 0
            for key, entry in data.items():
                try:
                    normalized = normalize_entry(key, entry)
                    obj, was_created = Item.objects.update_or_create(
                        external_id=normalized['external_id'],
                        source=src,
                        defaults={
                            'situation': normalized['situation'],
                            'titles': normalized['titles'],
                            'characters': normalized['characters'],
                            'artist': normalized['artist'],
                            'link': normalized['link'],
                            'tags': normalized['tags'],
                            'source': src,
                        }
                    )
                    if was_created:
                        created += 1
                    else:
                        updated += 1
                except Exception as e:
                    errors += 1
                    self.stdout.write(self.style.ERROR(f'Failed to import {key}: {e}'))

            total_created += created
            total_updated += updated
            total_errors += errors

            self.stdout.write(self.style.SUCCESS(f'File {path}: created={created} updated={updated} errors={errors}'))

        self.stdout.write(self.style.SUCCESS(f'Import summary: created={total_created} updated={total_updated} errors={total_errors} (files={len(resolved)})'))
