"""One-time (but safely re-runnable) fixup for Item.character_regions rows
still saved in the OLD, pre-multi-character-per-box schema:
`{"box": [x1,y1,x2,y2], "character": "name"}` — a single string under the
key `character` (singular), and no `image_index` key at all. Every current
code path (RegionAnnotator.jsx's pre-fill, character_regions_view,
region_label_queue, region_mismatch_queue, train_character_classifier's
_get_manual_labeled_rows) only reads the CURRENT schema:
`{"image_index": int|null, "box": [...], "characters": [str, ...]}` (plural
list) — so an old-format row silently reads back as if it had ZERO
characters (`r.get('characters')` is None for it), even though the box
itself and its one label are still sitting right there in the JSON. This is
exactly the bug reported as "領域指定キューとは整合していない" — inspecting
the raw API response confirmed the stored rows really do have the old
`{"box": ..., "character": "name"}` shape.

No code path in this app writes the old shape anymore (that ended when
multi-character-per-box support was added), so this is purely historical
data — either items labeled before that change, or (the reason this is a
management command a human can re-run rather than a one-shot data
migration) items restored from a Google Drive backup (see drive_backup.py)
taken before it, which would silently reintroduce the old shape without
ever re-running a migration.

Usage:
  docker compose exec web python manage.py normalize_character_regions
  docker compose exec web python manage.py normalize_character_regions --dry-run
"""
from django.core.management.base import BaseCommand

from item.models import Item


class Command(BaseCommand):
    help = (
        'Converts any Item.character_regions entries still in the old '
        '{"box":[...], "character": "name"} shape into the current '
        '{"image_index": null, "box":[...], "characters": ["name"]} shape. '
        'Safe to re-run (a no-op once everything is already normalized) — '
        'e.g. after restoring a pre-multi-character-per-box backup.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be converted without actually saving anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        items_changed = 0
        regions_converted = 0

        for item in Item.objects.exclude(character_regions=[]).iterator():
            regions = item.character_regions or []
            new_regions = []
            item_changed = False
            for r in regions:
                if not isinstance(r, dict):
                    new_regions.append(r)
                    continue
                if 'characters' in r:
                    new_regions.append(r)
                    continue
                # Old shape: singular 'character' string, no 'image_index'.
                name = r.get('character')
                new_regions.append({
                    'image_index': r.get('image_index'),
                    'box': r.get('box'),
                    'characters': [name] if name else [],
                })
                item_changed = True
                regions_converted += 1
            if item_changed:
                items_changed += 1
                if not dry_run:
                    item.character_regions = new_regions
                    item.save(update_fields=['character_regions'])

        verb = 'Would convert' if dry_run else 'Converted'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {regions_converted} region(s) across {items_changed} item(s).'
        ))
