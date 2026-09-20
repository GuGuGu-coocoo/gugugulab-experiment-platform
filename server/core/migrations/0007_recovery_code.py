import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Additive 03D short-code recovery tickets.

    The table is new, so every existing session keeps its original long-permit
    recovery contract; no legacy row is rewritten or backfilled.
    """

    dependencies = [('core', '0006_study_publication')]

    operations = [
        migrations.CreateModel(
            name='RecoveryCode',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code_hash', models.CharField(max_length=64, unique=True)),
                ('expires_at', models.DateTimeField()),
                ('consumed', models.BooleanField(default=False)),
                ('superseded', models.BooleanField(default=False)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('last_attempt_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('issuer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='auth.user')),
                ('release', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.release')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.session')),
                ('study', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.study')),
            ],
        ),
    ]
