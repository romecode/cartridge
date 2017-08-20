# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion
import versatileimagefield.fields
import mezzanine.core.fields
import cartridge.shop.fields


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0002_auto_20170820_1845'),
        ('shop', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='cartitem',
            name='can_ship',
            field=models.BooleanField(default=False),
        ),
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
        migrations.AddField(
            model_name='product',
            name='list',
            field=mezzanine.core.fields.RichTextField(verbose_name='List', blank=True),
        ),
        migrations.AddField(
            model_name='product',
            name='required_permissions',
            field=models.ManyToManyField(to='auth.Permission', verbose_name='Required Permissions', blank=True),
        ),
        migrations.AddField(
            model_name='productimage',
            name='image',
            field=versatileimagefield.fields.VersatileImageField(upload_to='product', max_length=255, verbose_name='Image', blank=True),
        ),
        migrations.AddField(
            model_name='productimage',
            name='ppoi',
            field=versatileimagefield.fields.PPOIField(default='0.5x0.5', verbose_name='Image PPOI', max_length=20, editable=False),
        ),
        migrations.AddField(
            model_name='productvariation',
            name='option3',
            field=cartridge.shop.fields.OptionField(max_length=50, null=True, verbose_name='Automatic Renewal'),
        ),
        migrations.AlterField(
            model_name='order',
            name='billing_detail_email',
            field=models.EmailField(max_length=254, verbose_name='Email'),
        ),
        migrations.AlterField(
            model_name='order',
            name='key',
            field=models.CharField(max_length=40, db_index=True),
        ),
        migrations.AlterField(
            model_name='product',
            name='publish_date',
            field=models.DateTimeField(help_text="With Published chosen, won't be shown until this time", null=True, verbose_name='Published from', db_index=True, blank=True),
        ),
        migrations.AlterField(
            model_name='product',
            name='sku',
            field=cartridge.shop.fields.SKUField(max_length=20, null=True, verbose_name='SKU', blank=True),
        ),
        migrations.AlterField(
            model_name='productimage',
            name='_order',
            field=mezzanine.core.fields.OrderField(null=True, verbose_name='Order'),
        ),
        migrations.AlterField(
            model_name='productimage',
            name='file',
            field=mezzanine.core.fields.FileField(max_length=255, verbose_name='Image'),
        ),
        migrations.AlterField(
            model_name='productoption',
            name='type',
            field=models.IntegerField(verbose_name='Type', choices=[(1, 'Medium'), (2, 'Duration'), (3, 'Automatic Renewal')]),
        ),
        migrations.AlterField(
            model_name='productvariation',
            name='image',
            field=models.ForeignKey(on_delete=django.db.models.deletion.SET_NULL, verbose_name='Image', blank=True, to='shop.ProductImage', null=True),
        ),
        migrations.AlterField(
            model_name='productvariation',
            name='option1',
            field=cartridge.shop.fields.OptionField(max_length=50, null=True, verbose_name='Medium'),
        ),
        migrations.AlterField(
            model_name='productvariation',
            name='option2',
            field=cartridge.shop.fields.OptionField(max_length=50, null=True, verbose_name='Duration'),
        ),
        migrations.AlterField(
            model_name='productvariation',
            name='sku',
            field=cartridge.shop.fields.SKUField(max_length=20, null=True, verbose_name='SKU', blank=True),
        ),
        migrations.AlterUniqueTogether(
            name='product',
            unique_together=set([('sku', 'site')]),
        ),
    ]
