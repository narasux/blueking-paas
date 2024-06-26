# Generated by Django 3.2.12 on 2024-04-11 03:43

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('servicehub', '0002_auto_20201209_1647'),
    ]

    operations = [
        migrations.AddField(
            model_name='remoteserviceengineappattachment',
            name='credentials_disabled',
            field=models.BooleanField(default=False, verbose_name='是否禁止使用凭证'),
        ),
        migrations.AddField(
            model_name='serviceengineappattachment',
            name='credentials_disabled',
            field=models.BooleanField(default=False, verbose_name='是否禁止使用凭证'),
        ),
    ]
