from django.db import migrations


def clone_twitter_row_into_pixiv(apps, schema_editor):
    """PollerSettings used to be a single row shared by both pollers
    (before 0028 added `platform`) -- the previous migration stamped
    that existing row as platform='twitter' (its own migration default),
    so a 'pixiv' row never got created at all. Clone that row's settings
    into a new 'pixiv' row so an existing deployment's already-configured
    enabled/interval/items_per_tick/backfill_pages_per_tick carries over
    to Pixiv's now-independent settings too, instead of silently
    resetting Pixiv back to the model's own field defaults.
    """
    PollerSettings = apps.get_model('item', 'PollerSettings')
    twitter_row = PollerSettings.objects.filter(platform='twitter').first()
    if twitter_row is None:
        return
    PollerSettings.objects.get_or_create(
        platform='pixiv',
        defaults={
            'enabled': twitter_row.enabled,
            'items_per_tick': twitter_row.items_per_tick,
            'interval_value': twitter_row.interval_value,
            'interval_unit': twitter_row.interval_unit,
            'backfill_pages_per_tick': twitter_row.backfill_pages_per_tick,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('item', '0028_pollersettings_platform'),
    ]

    operations = [
        migrations.RunPython(clone_twitter_row_into_pixiv, migrations.RunPython.noop),
    ]
