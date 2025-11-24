from django.core.management.base import BaseCommand
import subprocess
import os
import json
from django.conf import settings


class Command(BaseCommand):
    help = "Call rust_worker to compute preview image sizes for an item"

    def add_arguments(self, parser):
        parser.add_argument('--item-id', type=int, required=True)
        parser.add_argument('--db-url', type=str, required=False, help='Optional DATABASE_URL override')
        parser.add_argument('--bin', type=str, required=False, help='Path to rust_worker binary (defaults to rust_worker/target/release/rust_worker)')

    def handle(self, *args, **options):
        item_id = options['item_id']
        db_url = options.get('db_url') or os.environ.get('DATABASE_URL')
        bin_path = options.get('bin') or os.path.join(settings.BASE_DIR, 'rust_worker', 'target', 'release', 'rust_worker')

        if not db_url:
            self.stderr.write('DATABASE_URL not set and --db-url not provided')
            return

        if not os.path.exists(bin_path):
            self.stderr.write(f'Binary not found at {bin_path}. Build with `cargo build --release` in rust_worker/`')
            return

        cmd = [bin_path, '--item-id', str(item_id), '--db-url', db_url]
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        except subprocess.CalledProcessError as e:
            self.stderr.write('Rust worker failed:')
            self.stderr.write(e.output)
            return

        try:
            parsed = json.loads(out)
        except Exception as e:
            self.stderr.write('Failed to parse JSON output from rust worker')
            self.stderr.write(str(e))
            self.stderr.write('Raw output:')
            self.stderr.write(out)
            return

        self.stdout.write(json.dumps(parsed, indent=2))
