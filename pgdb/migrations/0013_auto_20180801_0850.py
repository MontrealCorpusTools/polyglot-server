# Generated by Django 2.0 on 2018-08-01 13:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pgdb', '0012_merge_20180712_1649'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='query',
            options={'verbose_name_plural': 'Queries'},
        ),
        migrations.AddField(
            model_name='corpuspermissions',
            name='can_access_database',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='corpuspermissions',
            name='can_enrich',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='soundfile',
            name='consonant_freq_path',
            field=models.FilePathField(match='*.wav', max_length=250, path='/mnt/e/bestiary-data', recursive=True),
        ),
        migrations.AlterField(
            model_name='soundfile',
            name='file_path',
            field=models.FilePathField(match='*.wav', max_length=250, path='/mnt/e/Data/PolyglotData', recursive=True),
        ),
        migrations.AlterField(
            model_name='soundfile',
            name='low_freq_path',
            field=models.FilePathField(match='*.wav', max_length=250, path='/mnt/e/bestiary-data', recursive=True),
        ),
        migrations.AlterField(
            model_name='soundfile',
            name='vowel_freq_path',
            field=models.FilePathField(match='*.wav', max_length=250, path='/mnt/e/bestiary-data', recursive=True),
        ),
    ]
