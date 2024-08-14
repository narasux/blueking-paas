# Generated by Django 3.2.25 on 2024-07-31 03:47

from django.db import migrations, models
import paasng.utils.models


class Migration(migrations.Migration):

    dependencies = [
        ('engine', '0021_presetenvvariable'),
    ]

    operations = [
        migrations.CreateModel(
            name='BuiltinConfigVar',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('key', models.CharField(max_length=128, unique=True, verbose_name='环境变量名')),
                ('value', models.TextField(max_length=512, verbose_name='环境变量值')),
                ('description', models.CharField(max_length=512, verbose_name='描述')),
                ('operator', paasng.utils.models.BkUserField(blank=True, db_index=True, max_length=64, null=True, verbose_name='更新者')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]