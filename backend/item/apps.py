from django.apps import AppConfig


class ItemConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'item'

    def ready(self):
        try:
            from security.keyring_store import migrate_legacy_keys
            migrate_legacy_keys()
        except Exception:
            pass
