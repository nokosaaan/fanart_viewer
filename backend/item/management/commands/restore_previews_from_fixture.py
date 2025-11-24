from django.core.management.base import BaseCommand
from django.db import transaction
import json
import base64
import os

from item.models import Item, PreviewImage


class Command(BaseCommand):
    help = 'Restore PreviewImage and preview_data fields from a dumpdata JSON fixture'

    def add_arguments(self, parser):
        parser.add_argument('fixture', nargs='?', default='/app/backup/items-backup.json', help='Path to fixture JSON file (dumpdata output). Defaults to /app/backup/items-backup.json inside container')
        parser.add_argument('--dry-run', action='store_true', help='Do not write to DB; just report what would be done')

    def handle(self, *args, **options):
        path = options['fixture']
        dry = options['dry_run']

        if not os.path.exists(path):
            self.stderr.write(self.style.ERROR(f'Fixture not found: {path}'))
            return

        with open(path, 'rb') as fh:
            try:
                data = json.load(fh)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Failed to parse JSON: {e}'))
                return

        # Build mapping from old item PK -> fields dict
        oldpk_to_fields = {}
        for obj in data:
            if obj.get('model', '').lower().endswith('item'):
                pk = obj.get('pk')
                fields = obj.get('fields', {})
                oldpk_to_fields[pk] = fields

        restored_preview_images = 0
        restored_legacy_previews = 0
        skipped_missing = 0
        ambiguous = 0

        # helper: find single matching current Item using multiple heuristics
        def find_current_item(fields):
            ext = fields.get('external_id')
            src = fields.get('source') or fields.get('file') or fields.get('source_file')
            link = fields.get('link') or fields.get('url')
            title = fields.get('title') or (fields.get('titles')[0] if isinstance(fields.get('titles'), list) and fields.get('titles') else None)
            artist = fields.get('artist')

            # 1) exact external_id + source
            if ext and src:
                qs = Item.objects.filter(external_id=ext, source=src)
                if qs.count() == 1:
                    return qs.first(), 'ext+src'
                elif qs.count() > 1:
                    return None, 'ambiguous_ext_src'

            # 2) external_id alone
            if ext:
                qs = Item.objects.filter(external_id=ext)
                if qs.count() == 1:
                    return qs.first(), 'ext'
                elif qs.count() > 1:
                    # keep going to disambiguate with link/title
                    pass

            # 3) link exact match
            if link:
                qs = Item.objects.filter(link=link)
                if qs.count() == 1:
                    return qs.first(), 'link'
                elif qs.count() > 1:
                    return None, 'ambiguous_link'

            # 4) match by title + artist (case-insensitive contains)
            if title:
                title_q = title
                qs = Item.objects.all()
                qs = qs.filter(titles__icontains=title_q) if hasattr(Item, 'titles') else qs
                # if titles field not available as queryable, fallback to title exact
                # but DRF/Django models may store titles as JSON; this is heuristic
                if artist:
                    qs = qs.filter(artist__icontains=artist)
                if qs.count() == 1:
                    return qs.first(), 'title+artist'
                elif qs.count() > 1:
                    return None, 'ambiguous_title_artist'

            return None, 'not_found'

        # We'll create PreviewImage rows from fixture previewimage entries
        for obj in data:
            model = obj.get('model', '').lower()
            fields = obj.get('fields', {})
            if model.endswith('previewimage'):
                old_item_ref = fields.get('item')
                order = fields.get('order') or 0
                b64data = fields.get('data')
                content_type = fields.get('content_type')

                if b64data is None:
                    continue

                # find original item fields and then current Item
                old_fields = oldpk_to_fields.get(old_item_ref, {})
                cur_item, reason = find_current_item(old_fields)
                if reason and reason.startswith('ambiguous'):
                    ambiguous += 1
                    continue
                if not cur_item:
                    skipped_missing += 1
                    continue

                try:
                    raw = base64.b64decode(b64data)
                except Exception:
                    try:
                        raw = b64data.encode('utf-8')
                    except Exception:
                        continue

                if dry:
                    restored_preview_images += 1
                    continue

                # delete existing previews for this item at this order if any collision
                PreviewImage.objects.filter(item=cur_item, order=order).delete()
                PreviewImage.objects.create(item=cur_item, order=order, data=raw, content_type=content_type)
                restored_preview_images += 1

        # Also restore legacy preview_data field if present in item entries
        for oldpk, fields in oldpk_to_fields.items():
            b64preview = fields.get('preview_data')
            pct = fields.get('preview_content_type')
            if not b64preview:
                continue
            cur_item, reason = find_current_item(fields)
            if reason and reason.startswith('ambiguous'):
                ambiguous += 1
                continue
            if not cur_item:
                skipped_missing += 1
                continue

            try:
                raw = base64.b64decode(b64preview)
            except Exception:
                try:
                    raw = b64preview.encode('utf-8')
                except Exception:
                    continue

            if dry:
                restored_legacy_previews += 1
                continue

            # Only set legacy preview_data if no PreviewImage exists
            if not cur_item.preview_images.exists():
                cur_item.preview_data = raw
                cur_item.preview_content_type = pct
                cur_item.save()
                restored_legacy_previews += 1

        self.stdout.write(self.style.SUCCESS(f'Restored preview images: {restored_preview_images}'))
        self.stdout.write(self.style.SUCCESS(f'Restored legacy preview_data: {restored_legacy_previews}'))
        if skipped_missing:
            self.stdout.write(self.style.WARNING(f'Skipped entries with missing matching Item: {skipped_missing}'))
        if ambiguous:
            self.stdout.write(self.style.WARNING(f'Ambiguous matches skipped: {ambiguous}'))
