# Generated by Django 2.0 on 2018-06-10 04:03

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pgdb', '0009_auto_20180603_2049'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='corpus',
            name='source_directory',
        ),
        migrations.RemoveField(
            model_name='corpus',
            name='status',
        ),
        migrations.AddField(
            model_name='corpus',
            name='imported',
            field=models.BooleanField(default=False),
        ),
    ]
