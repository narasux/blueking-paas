# Generated by Django 3.2.12 on 2023-05-27 04:08

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('engine', '0013_auto_20230417_1113'),
    ]

    operations = [
        migrations.AddField(
            model_name='deployment',
            name='bkapp_revision_id',
            field=models.IntegerField(default=None, help_text='BkApp Revision id', null=True),
            preserve_default=False,
        ),
    ]
