from pathlib import Path
from django.core.management.base import BaseCommand
from security.keyring_store import migrate_legacy_keys


class Command(BaseCommand):
    help = 'Migrate legacy plaintext key files from ~/.fanart_viewer into OS Credential Manager'

    def handle(self, *args, **options):
        user_dir = Path.home() / '.fanart_viewer'
        migrated = migrate_legacy_keys(user_dir)
        if migrated:
            self.stdout.write(self.style.SUCCESS(
                f"移行完了: 以下のファイルをOS資格情報ストアへ格納し、削除しました:\n  " + "\n  ".join(migrated)
            ))
        else:
            self.stdout.write(f"{user_dir} に平文キーファイルは見つかりませんでした（既に移行済みか、存在しません）。")
