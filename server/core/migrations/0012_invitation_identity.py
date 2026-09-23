"""Additive invitation identity binding (R03 stable subject).

Two nullable/defaulted fields, no backfill: every existing invitation row keeps
``identity_version=1`` and ``principal=NULL`` and therefore keeps its original
username-only boundary until it is revoked or expires. Rows written by the
current issuance path carry ``identity_version=2``; an invitation for an account
that already existed binds that account's stable Principal, and a new-account
application is identified by its own invitation UUID. A bound Principal is
PROTECTed so a stable subject reference is never silently blanked.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_invitation_bound'),
    ]

    operations = [
        migrations.AddField(
            model_name='invitation',
            name='identity_version',
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='invitation',
            name='principal',
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='core.principal'),
        ),
    ]
