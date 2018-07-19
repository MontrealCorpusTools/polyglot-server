# Generated by Django 2.0.7 on 2018-07-11 16:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pgdb', '0010_auto_20180610_0403'),
    ]

    operations = [
        migrations.AlterField(
            model_name='soundfile',
            name='consonant_freq_path',
            field=models.FilePathField(match='*.wav', max_length=250, path='polyglot_data', recursive=True),
        ),
        migrations.AlterField(
            model_name='soundfile',
            name='file_path',
            field=models.FilePathField(match='*.wav', max_length=250, path='polyglot_source', recursive=True),
        ),
        migrations.AlterField(
            model_name='soundfile',
            name='low_freq_path',
            field=models.FilePathField(match='*.wav', max_length=250, path='polyglot_data', recursive=True),
        ),
        migrations.AlterField(
            model_name='soundfile',
            name='vowel_freq_path',
            field=models.FilePathField(match='*.wav', max_length=250, path='polyglot_data', recursive=True),
        ),
    ]
