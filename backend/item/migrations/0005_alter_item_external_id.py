from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('item', '0004_previewimage'),
    ]

    operations = [
        migrations.AlterField(
            model_name='item',
            name='external_id',
            field=models.BigIntegerField(),
        ),
    ]