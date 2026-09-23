"""Additive finite policy bound for account invitations (R02C).

One nullable field, no backfill: legacy invitation rows keep ``NULL`` (the
invited account keeps its role default) exactly as before. A stored bound is
only written by the v2 account entry when a non-Owner creates an Admin
invitation, and it is validated and re-verified against the issuer's current
policy at activation.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_policy_principal'),
    ]

    operations = [
        migrations.AddField(
            model_name='accountinvitation',
            name='bound_policy',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
