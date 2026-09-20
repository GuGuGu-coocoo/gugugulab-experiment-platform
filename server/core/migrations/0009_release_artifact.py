from django.db import migrations, models


class Migration(migrations.Migration):
    """03D: complete immutable distribution artifacts.

    The outer artifact digest/size of a released complete package is stored on
    the release row (never inside the artifact itself); existing releases keep
    empty values and their original admission and download contracts.
    """

    dependencies = [('core', '0008_audit_optional_actor')]

    operations = [
        migrations.AddField(
            model_name='release',
            name='artifact_path',
            field=models.CharField(blank=True, max_length=256),
        ),
        migrations.AddField(
            model_name='release',
            name='artifact_digest',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name='release',
            name='artifact_size',
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
