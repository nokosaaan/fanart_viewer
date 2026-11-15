"""Management command to compress existing PreviewImage rows to WebP.

Downscales oversized images and recompresses them to lean WebP format,
dramatically reducing SQLite database file size and memory footprint.
"""

import json
import os
import sys
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from item.models import Item, PreviewImage
from item.image_optimizer import optimize_image_data, DEFAULT_MAX_DIM, DEFAULT_QUALITY


class Command(BaseCommand):
    help = "Compress existing PreviewImage rows in database to WebP to reduce DB size."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-dim",
            type=int,
            default=DEFAULT_MAX_DIM,
            help=f"Maximum image width/height in pixels (default: {DEFAULT_MAX_DIM})",
        )
        parser.add_argument(
            "--quality",
            type=int,
            default=DEFAULT_QUALITY,
            help=f"WebP quality 1-100 (default: {DEFAULT_QUALITY})",
        )
        parser.add_argument(
            "--item-id",
            type=int,
            default=None,
            help="Compress only preview images for this specific item ID",
        )
        parser.add_argument(
            "--min-size-kb",
            type=int,
            default=0,
            help="Only process images larger than this size in KB (default: 0)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of images to process per transaction batch (default: 50)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Calculate savings without modifying database",
        )
        parser.add_argument(
            "--vacuum",
            action="store_true",
            help="Run SQLite VACUUM after compression to reclaim disk space",
        )
        parser.add_argument(
            "--no-cache",
            action="store_true",
            help="Ignore processed-ID cache and re-examine all images",
        )

    def _cache_file_path(self, db_target):
        if db_target and db_target != ":memory:":
            p = Path(db_target)
            if p.parent.exists():
                return p.parent / ".compress_previews_cache.json"
        return None

    def _load_cache(self, cache_path, cache_key):
        if not cache_path or not cache_path.exists():
            return {}, set()
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}, set()
            ids = data.get(cache_key, [])
            return data, set(int(x) for x in ids if isinstance(x, int))
        except Exception:
            return {}, set()

    def _save_cache(self, cache_path, cache_data, cache_key, id_set):
        if not cache_path:
            return
        try:
            cache_data[cache_key] = sorted(id_set)
            tmp_path = Path(str(cache_path) + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f)
            os.replace(tmp_path, cache_path)
        except Exception:
            pass

    def handle(self, *args, **options):
        max_dim = options["max_dim"]
        quality = options["quality"]
        item_id = options["item_id"]
        min_size_bytes = options["min_size_kb"] * 1024
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]
        do_vacuum = options["vacuum"]
        no_cache = options["no_cache"]

        qs = PreviewImage.objects.all().order_by("id")
        if item_id is not None:
            qs = qs.filter(item_id=item_id)

        all_ids = list(qs.values_list("id", flat=True))
        total_count = len(all_ids)
        if total_count == 0:
            self.stdout.write(self.style.WARNING("No PreviewImage records found."))
            return

        mode_str = "DRY RUN" if dry_run else "LIVE RUN"
        db_settings = connection.settings_dict
        engine_short = db_settings.get("ENGINE", "").rsplit(".", 1)[-1]
        db_target = db_settings.get("NAME", "")
        cache_path = None if no_cache else self._cache_file_path(db_target)
        cache_key = f"{max_dim}_{quality}"
        cache_data, cached_ids = self._load_cache(cache_path, cache_key) if cache_path else ({}, set())

        target_ids = [pid for pid in all_ids if pid not in cached_ids]
        cache_skipped = total_count - len(target_ids)

        self.stdout.write(f"Database target: {engine_short} ({db_target})")
        self.stdout.write(
            f"Starting PreviewImage compression [{mode_str}] (Total: {total_count} images, "
            f"cached-skip: {cache_skipped}, to-check: {len(target_ids)}, "
            f"max_dim={max_dim}px, quality={quality}, min_size={options['min_size_kb']}KB)..."
        )

        total_orig_bytes = 0
        total_opt_bytes = 0
        compressed_count = 0
        skipped_count = cache_skipped

        # Process uncached IDs in batches so image binary data is only loaded for unchecked rows
        for i in range(0, len(target_ids), batch_size):
            chunk_ids = target_ids[i:i + batch_size]
            batch_checked_ids = []
            for p in PreviewImage.objects.filter(id__in=chunk_ids).order_by("id").only("id", "item_id", "order", "data", "content_type"):
                raw_data = bytes(p.data) if p.data else b""
                orig_len = len(raw_data)
                total_orig_bytes += orig_len

                if orig_len == 0 or orig_len < min_size_bytes:
                    total_opt_bytes += orig_len
                    skipped_count += 1
                    if orig_len == 0:
                        batch_checked_ids.append(p.id)
                    continue

                opt_data, opt_ctype = optimize_image_data(
                    raw_data,
                    content_type=p.content_type,
                    max_dim=max_dim,
                    quality=quality,
                )
                opt_len = len(opt_data)

                if opt_len < orig_len:
                    total_opt_bytes += opt_len
                    compressed_count += 1
                    saved_kb = (orig_len - opt_len) / 1024
                    if not dry_run:
                        PreviewImage.objects.filter(pk=p.id).update(data=opt_data, content_type=opt_ctype)

                    if compressed_count % 25 == 0 or compressed_count == 1:
                        self.stdout.write(
                            f"  Processed {compressed_count} images (item #{p.item_id} p#{p.order}: "
                            f"{orig_len // 1024}KB -> {opt_len // 1024}KB, -{saved_kb:.1f}KB)"
                        )
                else:
                    total_opt_bytes += orig_len
                    skipped_count += 1

                batch_checked_ids.append(p.id)

            if not dry_run and cache_path and batch_checked_ids:
                cached_ids.update(batch_checked_ids)
                self._save_cache(cache_path, cache_data, cache_key, cached_ids)

        # Also compress legacy Item.preview_data if any
        legacy_qs = Item.objects.filter(preview_data__isnull=False)
        if item_id is not None:
            legacy_qs = legacy_qs.filter(id=item_id)
        legacy_compressed = 0
        for it in legacy_qs.only("id", "preview_data", "preview_content_type").iterator(chunk_size=batch_size):
            raw_data = bytes(it.preview_data) if it.preview_data else b""
            orig_len = len(raw_data)
            total_orig_bytes += orig_len
            if orig_len > min_size_bytes:
                opt_data, opt_ctype = optimize_image_data(
                    raw_data,
                    content_type=it.preview_content_type,
                    max_dim=max_dim,
                    quality=quality,
                )
                opt_len = len(opt_data)
                if opt_len < orig_len:
                    total_opt_bytes += opt_len
                    legacy_compressed += 1
                    if not dry_run:
                        Item.objects.filter(pk=it.id).update(
                            preview_data=opt_data,
                            preview_content_type=opt_ctype,
                        )
                else:
                    total_opt_bytes += orig_len
            else:
                total_opt_bytes += orig_len

        orig_mb = total_orig_bytes / (1024 * 1024)
        opt_mb = total_opt_bytes / (1024 * 1024)
        saved_mb = (total_orig_bytes - total_opt_bytes) / (1024 * 1024)
        pct = ((total_orig_bytes - total_opt_bytes) / total_orig_bytes * 100) if total_orig_bytes > 0 else 0

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS(f"Finished Preview Compression [{mode_str}]"))
        self.stdout.write(f"  Images examined   : {total_count}")
        self.stdout.write(f"  Images compressed : {compressed_count} (legacy items: {legacy_compressed})")
        self.stdout.write(f"  Images unchanged  : {skipped_count}")
        self.stdout.write(f"  Original size     : {orig_mb:.2f} MB")
        self.stdout.write(f"  Optimized size    : {opt_mb:.2f} MB")
        self.stdout.write(self.style.SUCCESS(f"  Space saved       : {saved_mb:.2f} MB ({pct:.1f}% reduction)"))
        self.stdout.write("=" * 60)

        # Run VACUUM if requested or if live run on SQLite
        if not dry_run and do_vacuum:
            if connection.vendor == "sqlite":
                self.stdout.write("Running SQLite VACUUM to reclaim disk space from filesystem...")
                cursor = connection.cursor()
                cursor.execute("VACUUM;")
                self.stdout.write(self.style.SUCCESS("SQLite VACUUM complete. Database file shrunk."))
            else:
                self.stdout.write(f"Database vendor is {connection.vendor}; VACUUM skipped.")
