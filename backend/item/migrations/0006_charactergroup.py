from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('item', '0005_alter_item_external_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='CharacterGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, unique=True)),
                ('characters', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
    ]
