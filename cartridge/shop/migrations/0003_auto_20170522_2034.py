# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0002_auto_20170522_2033'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='billing_detail_business',
            field=models.CharField(max_length=125, verbose_name='Business Name', blank=True),
        ),
        migrations.AddField(
            model_name='order',
            name='express',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='order',
            name='shipping_detail_business',
            field=models.CharField(max_length=125, verbose_name='Business Name', blank=True),
        ),
    ]
