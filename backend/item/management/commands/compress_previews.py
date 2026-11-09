"""Management command to compress existing PreviewImage rows to WebP.

Downscales oversized images and recompresses them to lean WebP format,
dramatically reducing SQLite database file size and memory footprint.
"""

import sys
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

    def handle(self, *args, **options):
        max_dim = options["max_dim"]
        quality = options["quality"]
        item_id = options["item_id"]
        min_size_bytes = options["min_size_kb"] * 1024
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]
        do_vacuum = options["vacuum"]

        qs = PreviewImage.objects.all().order_by("id")
        if item_id is not None:
            qs = qs.filter(item_id=item_id)

        total_count = qs.count()
        if total_count == 0:
            self.stdout.write(self.style.WARNING("No PreviewImage records found."))
            return

        mode_str = "DRY RUN" if dry_run else "LIVE RUN"
        db_settings = connection.settings_dict
        engine_short = db_settings.get("ENGINE", "").rsplit(".", 1)[-1]
        db_target = db_settings.get("NAME", "")
        self.stdout.write(f"Database target: {engine_short} ({db_target})")
        self.stdout.write(
            f"Starting PreviewImage compression [{mode_str}] (Total: {total_count} images, "
            f"max_dim={max_dim}px, quality={quality}, min_size={options['min_size_kb']}KB)..."
        )

        total_orig_bytes = 0
        total_opt_bytes = 0
        compressed_count = 0
        skipped_count = 0

        # Process in batches using .only() to stream image binary data
        for p in qs.only("id", "item_id", "order", "data", "content_type").iterator(chunk_size=batch_size):
            raw_data = bytes(p.data) if p.data else b""
            orig_len = len(raw_data)
            total_orig_bytes += orig_len

            if orig_len == 0 or orig_len < min_size_bytes:
                total_opt_bytes += orig_len
                skipped_count += 1
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
