"""Initialize a new, empty synthetic volume; never repair or overwrite existing data."""
import argparse
import json
import os
from pathlib import Path
import secrets
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--owner', default='synthetic_owner')
    args = parser.parse_args()
    root = Path(args.data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(root.iterdir()):
        raise SystemExit('Refusing to initialize a nonempty volume')
    # Exclusive marker also prevents concurrent initializers. On failure preserve it
    # and all partial data for inspection; serving still requires the final marker.
    descriptor = os.open(root / 'initializing', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    os.umask(0o077)
    secret = secrets.token_urlsafe(48)
    os.environ.update(DJANGO_SETTINGS_MODULE='gep.settings', GEP_DATA_DIR=str(root), GEP_SECRET_KEY=secret)
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from core.models import Instance
    call_command('migrate', verbosity=0)
    password = secrets.token_urlsafe(24)
    owner = get_user_model().objects.create_user(args.owner, password=password)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    for name, value in [('secret', secret), ('dev_credentials.json', json.dumps({'username': args.owner, 'password': password})), ('instance', str(instance.instance_id))]:
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
    (root / 'initializing').unlink()
    print('Initialized synthetic volume. Owner credentials are in dev_credentials.json; do not publish them.')


if __name__ == '__main__':
    main()
