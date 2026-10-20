"""Initialize a new, empty synthetic volume; never repair or overwrite existing data."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--owner', default='synthetic_owner')
    parser.add_argument('--instance', type=uuid.UUID)
    parser.add_argument('--setup-stdin', action='store_true', help='Read first-run inputs privately from stdin')
    args = parser.parse_args()
    inputs = None
    if args.setup_stdin:
        from .deployment import setup_values
        inputs = json.load(sys.stdin)
        try:
            args.owner, password, domain = setup_values(inputs)
            port = int(os.environ.get('GEP_LOCAL_PORT', '8080'))
            if not 1024 <= port <= 65535:
                raise ValueError('Invalid local port')
        except ValueError as error:
            raise SystemExit(str(error)) from None
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
    from core import researcher_passwords
    from core.models import AccountProfile, Instance, Principal
    call_command('migrate', verbosity=0)
    if inputs is None:
        password = researcher_passwords.generate_temporary_password()
    owner = get_user_model().objects.create_user(args.owner, password=password)
    # A new isolated volume is explicitly v2 and creates the stable principal of
    # its Owner; existing volumes are never reinitialized.
    instance = Instance.objects.create(instance_id=args.instance or uuid.uuid4(), owner=owner, authorization_version=2)
    Principal.objects.create(user=owner)
    AccountProfile.objects.create(user=owner, role='user', must_change_password=False, auth_version=1,
                                  revision=0, policy_version=2)
    files = [('secret', secret), ('instance', str(instance.instance_id))]
    if inputs is None:
        files.append(('dev_credentials.json', json.dumps({'username': args.owner, 'password': password})))
    else:
        from .deployment import domain_config, domain_files
        files.append(('deployment.json', json.dumps(domain_config(domain, port), ensure_ascii=False, indent=2)))
        if domain:
            files.extend(domain_files(domain).items())
    for name, value in files:
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
    (root / 'initializing').unlink()
    if inputs is None:
        print('Initialized synthetic volume. Owner credentials are in dev_credentials.json; do not publish them.')
    else:
        print('First-run setup complete. Owner password was not saved as plaintext.')


if __name__ == '__main__':
    main()
