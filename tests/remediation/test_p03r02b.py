"""Bounded in-memory token channel regression checks."""
import os
import subprocess
import threading

def _bounded(callable_, seconds=30):
    box = {}

    def target():
        try:
            box['value'] = callable_()
        except BaseException as error:  # noqa: BLE001 - recorded and re-raised below
            box['error'] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), 'helper did not return within the outer bound'
    if 'error' in box:
        raise box['error']
    return box.get('value')

WRITE_TOKEN = "import fs from 'node:fs'; fs.writeSync(Number(process.env.GEP_TOKEN_FD), 'tok-abc\\n');"

QUIET_EXIT = 'process.exit(0);'

def test_run_chrome_tokens_normal_nonzero_quiet_and_timeout_are_bounded(evidence, run_chrome_tokens):
    env = dict(os.environ)
    result, tokens = _bounded(lambda: run_chrome_tokens(WRITE_TOKEN, env, timeout=60))
    assert result.returncode == 0 and tokens == ['tok-abc']
    assert 'tok-abc' not in result.stdout and 'tok-abc' not in result.stderr

    nonzero = _bounded(lambda: run_chrome_tokens("import fs from 'node:fs'; "
                                                 "fs.writeSync(Number(process.env.GEP_TOKEN_FD), 'tok-n\\n'); "
                                                 "process.exit(3);", env, timeout=60))
    assert nonzero[0].returncode == 3 and nonzero[1] == ['tok-n']

    quiet = _bounded(lambda: run_chrome_tokens(QUIET_EXIT, env, timeout=60))
    assert quiet[0].returncode == 0 and quiet[1] == []

    def hanging():
        try:
            run_chrome_tokens('setTimeout(() => {}, 30000);', env, timeout=1)
        except subprocess.TimeoutExpired:
            return 'timeout'
        return 'returned'

    assert _bounded(hanging, seconds=30) == 'timeout'

    before = len(os.listdir('/dev/fd')) if os.path.isdir('/dev/fd') else None
    for _ in range(10):
        _bounded(lambda: run_chrome_tokens(QUIET_EXIT, env, timeout=60))
    after = len(os.listdir('/dev/fd')) if os.path.isdir('/dev/fd') else None
    if before is not None:
        assert after <= before + 2, (before, after)
    evidence('run_chrome_tokens.json', {'normal': tokens, 'nonzero': nonzero[0].returncode,
                                        'quiet': quiet[1], 'timeout': 'bounded',
                                        'descriptors_before': before, 'descriptors_after': after})

import importlib
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from core.models import Audit, Principal, Study

OWNER_PASSWORD = 'synthetic-p03r02b-owner-password'

def make_user(username, **kwargs):
    return get_user_model().objects.create_user(username, password=OWNER_PASSWORD, **kwargs)

def make_study(title):
    return Study.objects.create(title=title)

def test_migration_backfills_principals_and_audit_without_guessing_creator(db, evidence):
    User = get_user_model()
    users = [make_user('p03r02b_backfill_a'), make_user('p03r02b_backfill_b')]
    studies = [make_study('P03R02B backfill A'), make_study('P03R02B backfill B')]
    audits = [Audit.objects.create(actor=user, action='permission.matrix_changed', target=f'{user.pk}:{studies[0].pk}')
              for user in users]
    Audit.objects.create(actor=None, action='recovery.named_redeemed', target=str(studies[1].pk))

    migration = importlib.import_module('core.migrations.0010_policy_principal')
    migration.create_principals_and_backfill_audit(django_apps, None)
    migration.create_principals_and_backfill_audit(django_apps, None)  # idempotent

    principals = {principal.user_id: principal for principal in Principal.objects.all()}
    assert set(principals) == {user.pk for user in users}
    assert len(principals) == len(users)
    for audit in audits:
        audit.refresh_from_db()
        assert audit.actor_principal_id == principals[audit.actor_id].pk
    assert Audit.objects.filter(actor__isnull=True, actor_principal__isnull=True).count() == 1
    assert not Study.objects.exclude(creator_principal__isnull=True).exists()
    evidence('principal_backfill.json', {
        'principals': len(principals), 'backfilled_audits': len(audits),
        'device_audit_without_principal': 1, 'legacy_creator_null': True})
