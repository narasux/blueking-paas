# Generated by Django 3.2.12 on 2024-04-02 08:04

import blue_krill.models.fields
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('modules', '0012_appslugbuilder_step_meta_set'),
        ('engine', '0020_auto_20231218_1740'),
    ]

    operations = [
        migrations.CreateModel(
            name='PresetEnvVariable',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('environment_name', models.CharField(choices=[('stag', '仅测试环境'), ('prod', '仅生产环境'), ('_global_', '所有环境')], max_length=16, verbose_name='环境名称')),
                ('key', models.CharField(max_length=128)),
                ('value', blue_krill.models.fields.EncryptField()),
                ('module', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='modules.module', db_constraint=False)),
            ],
            options={
                'unique_together': {('module', 'environment_name', 'key')},
            },
        ),
    ]
