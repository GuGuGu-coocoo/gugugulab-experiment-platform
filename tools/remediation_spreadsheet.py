#!/usr/bin/env python
"""Spreadsheet-viewing preparation for the export v2 CSV pair (P03R08).

Run:

    .venv/bin/python tools/remediation_spreadsheet.py --verify-preparation

The tool never touches the development instance. It builds its own synthetic
SQLite database under a brand-new, unique evidence root and drives the real
``core.exports`` rendering path to write the two CSV members of the v2 bundle
(``participants.csv`` and ``events.csv``). Those files - or a ZIP downloaded by
a real browser via ``--zip`` - are then parsed with independent stdlib
``csv``/``json`` parsers and checked against the fixed synthetic values:
UTF-8 BOM, the frozen headers, ``text:001`` and ``text:=1+1`` text cells, a
literal newline inside one quoted field, a null roster code as an empty cell, a
large cell, and technical statuses without any scoring column.

It also performs a read-only discovery of the spreadsheet software that really
exists on this Mac and - only when ``GEP_TEST_SSH_HOST`` is configured - on
that authorised Windows host (Excel / WPS / LibreOffice versions), and records
the exact R11 plan for the real on-screen viewing. The Windows connection
settings (``GEP_TEST_SSH_HOST``, optional ``GEP_TEST_SSH_HOST_KEY_ALIAS`` and
``GEP_TEST_WINDOWS_ROOT``) come from the runtime environment only, are passed
to ``ssh`` as argv elements and are never written into this repository. Without
a configured host no network access is attempted at all: the Windows side is
recorded as ``NOT_CONFIGURED``/``NOT_RUN`` while the local file preparation
continues independently. ``openpyxl`` is never treated as an actual spreadsheet
application: this task's preparation records the facts and the plan, and the
mandatory real viewing stays an R11 gate. Missing software is reported as
``BLOCKED`` with guidance; it never blocks the other independent work and is
never reported as a software pass.

Every run keeps its synthetic database, CSV files and JSON report, successful
or failed, and a machine-readable report is always written. The report carries
versions, byte counts, hashes and error codes only - never a payload byte.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
import plistlib
import secrets
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_BASE = REPO_ROOT / 'local_data' / 'phase03_remediation_20260923'
SSH_HOST_ENV = 'GEP_TEST_SSH_HOST'
SSH_HOST_KEY_ALIAS_ENV = 'GEP_TEST_SSH_HOST_KEY_ALIAS'
WINDOWS_ROOT_ENV = 'GEP_TEST_WINDOWS_ROOT'
# The host and its optional key alias are supplied at run time only. Strict
# host-key checking, BatchMode and the bounded connect timeout always apply:
# nothing here ever edits the global SSH configuration, trust store or
# firewall, and no shell ever parses a supplied value.
SSH_OPTIONS = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
               '-o', 'StrictHostKeyChecking=yes']
MACOS_APPS = (('Microsoft Excel', '/Applications/Microsoft Excel.app'),
              ('WPS Office', '/Applications/wpsoffice.app'),
              ('WPS Office', '/Applications/WPS Office.app'),
              ('LibreOffice', '/Applications/LibreOffice.app'))
CSV_HEADER_PARTICIPANTS = ('研究标题（study_title）,研究 ID（study_id）,被试 UUID（participant_uuid）,'
                           '名单 ID（participant_code）,会话数（session_count）,已完成会话数（complete_session_count）,'
                           '记录数（event_count）,首次会话创建时间（first_session_created_at）,'
                           '最近会话创建时间（last_session_created_at）,技术状态（technical_status）,'
                           '会话列表 JSON（sessions_json）')
CSV_HEADER_EVENTS = ('研究标题（study_title）,研究 ID（study_id）,被试 UUID（participant_uuid）,'
                     '名单 ID（participant_code）,会话 ID（session_id）,发行 ID（release_id）,构建 ID（build_id）,'
                     '事件 ID（event_id）,分段 ID（segment_id）,序号（sequence）,事件类型（event_type）,'
                     'Schema ID（schema_id）,Schema 版本（schema_version）,接收时间（received_at）,'
                     '原始记录 JSON（record_json）')
LARGE_CELL_CHARS = 30000
FORMULA_TEXT = '=1+1'


def utc_stamp():
    return datetime.now(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def evidence_root(explicit):
    """A brand-new unique root strictly inside the dedicated evidence base.

    Every path component from the base down to the leaf is checked for symlinks
    before anything is resolved or created, the leaf must not exist at all (an
    existing empty directory is refused too), and only the leaf is created.
    """
    base = EVIDENCE_BASE
    if explicit:
        root = Path(explicit).expanduser()
        if not root.is_absolute():
            root = REPO_ROOT / root
    else:
        root = base / 'p03r08p' / f'{utc_stamp()}-{secrets.token_hex(4)}'
    try:
        relative = root.relative_to(base)
    except ValueError:
        raise SystemExit(f'refusing an evidence root outside {base}: {root}')
    if not relative.parts or any(part in ('..', '') for part in relative.parts):
        raise SystemExit(f'refusing a non-canonical evidence root: {root}')
    if base.is_symlink():
        raise SystemExit(f'refusing a symlinked evidence base: {base}')
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SystemExit(f'refusing a symlinked evidence path component: {current}')
    if root.exists():
        raise SystemExit(f'refusing an existing evidence root: {root}')
    root.mkdir(parents=True, exist_ok=False)
    return root


def setup_django(data_dir):
    sys.path.insert(0, str(REPO_ROOT / 'server'))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'gep.settings'
    os.environ['GEP_DATA_DIR'] = str(data_dir)
    os.environ['GEP_SECRET_KEY'] = secrets.token_urlsafe(32)
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate', verbosity=0, interactive=False)


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


class Report:
    def __init__(self):
        self.checks = []

    def check(self, name, ok, detail=None):
        self.checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    def failures(self):
        return [row['name'] for row in self.checks if not row['ok']]

    @property
    def ok(self):
        return not self.failures()


def build_synthetic_world(models):
    """One small study with every CSV round-trip edge the viewing gate needs."""
    from django.contrib.auth import get_user_model
    owner = get_user_model().objects.create_user('spreadsheet_owner', password=secrets.token_urlsafe(24))
    models.Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    study = models.Study.objects.create(title='Synthetic spreadsheet study\n第二行', recruitment='open', max_sessions=5)
    build = models.Build.objects.create(study=study, descriptor={'platform': 'synthetic', 'version': '1.0'},
                                        digest=secrets.token_hex(32))
    release = models.Release.objects.create(study=study, build=build, config={}, approved=True)
    participant_a = models.Participant.objects.create(study=study, code='001')
    models.Participant.objects.create(study=study, code=FORMULA_TEXT)
    models.Participant.objects.create(study=study, code=None)
    session = models.Session.objects.create(participant=participant_a, release=release, operation=uuid.uuid4(),
                                            proof_hash='p' * 48, request={'seed': 'spreadsheet'},
                                            token_hash='t' * 64,
                                            expires_at=datetime.now(dt_timezone.utc) + timedelta(days=1))
    event_id, segment_id = uuid.uuid4(), uuid.uuid4()
    models.Event.objects.create(
        session=session, event_id=event_id, segment_id=segment_id, sequence=1,
        envelope={'protocol_version': 'gep/1', 'event_id': str(event_id), 'session_id': str(session.id),
                  'segment_id': str(segment_id), 'sequence': 1, 'event_type': 'exp.rt',
                  'schema_id': 'rt', 'schema_version': '1',
                  'payload': {'formula': FORMULA_TEXT, 'text': 'line1\nline2', 'blob': 'a' * LARGE_CELL_CHARS}})
    session.completion = {'event_ids': [str(event_id)], 'segment_ids': [str(segment_id)]}
    session.save(update_fields=['completion'])
    return owner, study


def csv_rows(body):
    reader = csv.reader(io.StringIO(body.decode('utf-8-sig')))
    header = next(reader)
    keys = [column.split('（')[-1].rstrip('）') if '（' in column else column for column in header]
    return keys, [dict(zip(keys, row)) for row in reader]


def validate_pair(participants_bytes, events_bytes, *, participant_ids):
    """Independent parser checks of the frozen CSV pair against fixed values."""
    report = Report()
    report.check('participants_bom', participants_bytes.startswith(b'\xef\xbb\xbf'))
    report.check('events_bom', events_bytes.startswith(b'\xef\xbb\xbf'))
    header_participants = participants_bytes.decode('utf-8-sig').split('\r\n', 1)[0]
    header_events = events_bytes.decode('utf-8-sig').split('\r\n', 1)[0]
    report.check('participants_header', header_participants == CSV_HEADER_PARTICIPANTS, header_participants[:60])
    report.check('events_header', header_events == CSV_HEADER_EVENTS, header_events[:60])
    keys, people = csv_rows(participants_bytes)
    event_keys, events = csv_rows(events_bytes)
    report.check('participants_columns', keys == ['study_title', 'study_id', 'participant_uuid', 'participant_code',
                                                  'session_count', 'complete_session_count', 'event_count',
                                                  'first_session_created_at', 'last_session_created_at',
                                                  'technical_status', 'sessions_json'], keys)
    report.check('events_columns', event_keys == ['study_title', 'study_id', 'participant_uuid', 'participant_code',
                                                  'session_id', 'release_id', 'build_id', 'event_id', 'segment_id',
                                                  'sequence', 'event_type', 'schema_id', 'schema_version',
                                                  'received_at', 'record_json'], event_keys)
    report.check('roster_includes_non_participants',
                 {row['participant_uuid'] for row in people} == participant_ids)
    code_cells = {row['participant_code'] for row in people}
    report.check('leading_zero_code', 'text:001' in code_cells, sorted(code_cells))
    report.check('formula_text_cell', 'text:=1+1' in code_cells, sorted(code_cells))
    report.check('null_code_empty_cell', '' in code_cells)
    report.check('literal_newline_kept',
                 '"text:Synthetic spreadsheet study\n第二行"' in participants_bytes.decode('utf-8-sig'))
    report.check('technical_status_enum', all(row['technical_status'] in ('not_started', 'pending', 'complete')
                                              for row in people))
    report.check('no_scoring_column', not any(word in key for key in keys for word in ('score', 'valid', 'exclud')))
    large = [row for row in events if len(row['record_json']) > LARGE_CELL_CHARS]
    report.check('large_cell_kept', len(large) == 1 and len(large[0]['record_json']) > LARGE_CELL_CHARS,
                 len(large))
    if large:
        record = json.loads(large[0]['record_json'][len('json:'):])
        report.check('formula_round_trip', record['payload']['formula'] == FORMULA_TEXT)
        report.check('newline_round_trip', record['payload']['text'] == 'line1\nline2')
        report.check('large_cell_round_trip', len(record['payload']['blob']) == LARGE_CELL_CHARS)
    report.check('no_scoring_in_status', 'score' not in json.dumps(people))
    return report


def validate_zip(path, **kwargs):
    import zipfile
    report = Report()
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            report.check('zip_two_members', names == ['participants.csv', 'events.csv'], names)
            if names != ['participants.csv', 'events.csv']:
                return report
            report.check('zip_members_intact', archive.testzip() is None)
            participants_bytes = archive.read('participants.csv')
            events_bytes = archive.read('events.csv')
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        report.check('zip_readable', False, type(error).__name__)
        return report
    nested = validate_pair(participants_bytes, events_bytes, **kwargs)
    report.checks.extend(nested.checks)
    return report


def probe_macos():
    """Read-only discovery of installed spreadsheet applications on this Mac."""
    found = []
    for name, path in MACOS_APPS:
        app = Path(path)
        if not app.is_dir():
            continue
        version = ''
        plist = app / 'Contents' / 'Info.plist'
        if plist.is_file():
            try:
                with plist.open('rb') as stream:
                    version = str(plistlib.load(stream).get('CFBundleShortVersionString') or '')
            except (OSError, ValueError, plistlib.InvalidFileException):
                version = ''
        found.append({'name': name, 'path': path, 'version': version, 'usable_for_gui': name == 'Microsoft Excel'})
    return {'platform': 'macos', 'applications': found,
            'soffice_on_path': shutil.which('soffice') or ''}


WINDOWS_PROBE = (
    "$out=@(); "
    "$excel=(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\excel.exe' "
    "-ErrorAction SilentlyContinue).'(default)'; "
    "if($excel){$v=(Get-Item $excel).VersionInfo; $out+=('excel|'+$excel+'|'+$v.ProductVersion)}; "
    "Get-ChildItem 'HKLM:\\SOFTWARE\\Kingsoft','HKCU:\\Software\\Kingsoft' -ErrorAction SilentlyContinue | "
    "ForEach-Object {$out+=('wps-registry|'+$_.Name)}; "
    "Get-ChildItem 'HKLM:\\SOFTWARE\\LibreOffice','HKCU:\\Software\\LibreOffice' -ErrorAction SilentlyContinue | "
    "ForEach-Object {$out+=('libreoffice-registry|'+$_.Name)}; "
    "foreach($p in @(\"$env:ProgramFiles\\LibreOffice\\program\\soffice.exe\","
    "\"${env:ProgramFiles(x86)}\\LibreOffice\\program\\soffice.exe\","
    "\"$env:LOCALAPPDATA\\Kingsoft\\WPS Office\\office6\\wps.exe\","
    "\"$env:ProgramFiles\\Microsoft Office\\root\\Office16\\EXCEL.EXE\")){ "
    "if(Test-Path $p){$v=(Get-Item $p).VersionInfo; $out+=('file|'+$p+'|'+$v.ProductVersion)} }; "
    "Write-Output ($out -join \"`n\")"
)


def windows_config(environ=None):
    """The Windows connection settings, read from the runtime environment only.

    Every value is optional; a missing or blank value counts as not configured,
    so no default host, key alias or Windows directory is ever assumed.
    """
    env = os.environ if environ is None else environ
    return {'host': (env.get(SSH_HOST_ENV) or '').strip(),
            'host_key_alias': (env.get(SSH_HOST_KEY_ALIAS_ENV) or '').strip(),
            'windows_root': (env.get(WINDOWS_ROOT_ENV) or '').strip()}


def ssh_command(host, host_key_alias, probe=WINDOWS_PROBE):
    """The exact read-only ssh argv for a configured host.

    ``StrictHostKeyChecking=yes`` is always present. The optional key alias
    pins the known-host entry when the configured name differs from it; when it
    is omitted, the normal known_hosts check of the configured name applies.
    Supplied values are argv elements - never a shell string - and the remote
    command is the fixed, base64-encoded PowerShell probe above.
    """
    encoded = base64.b64encode(probe.encode('utf-16-le')).decode('ascii')
    options = list(SSH_OPTIONS)
    if host_key_alias:
        options += ['-o', f'HostKeyAlias={host_key_alias}']
    return ['ssh', *options, host, f'powershell -NoProfile -EncodedCommand {encoded}']


def probe_windows(config=None):
    """Read-only discovery on the configured Windows host; never a config change.

    Without ``GEP_TEST_SSH_HOST`` no subprocess is started at all: the result
    says ``NOT_CONFIGURED``/``NOT_RUN`` and the local preparation continues. A
    configured host runs the fixed read-only probe through the strict argv.
    """
    config = windows_config() if config is None else config
    base = {'platform': 'windows', 'configured': bool(config.get('host')),
            'probe': 'NOT_RUN', 'reachable': None, 'facts': [], 'excel': None,
            'wps_usable': False, 'libreoffice_usable': False,
            'libreoffice_registry_remnant': False}
    if not config.get('host'):
        return {**base, 'status': 'NOT_CONFIGURED'}
    command = ssh_command(config['host'], config.get('host_key_alias') or '')
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {**base, 'status': 'UNREACHABLE', 'reachable': False, 'error': type(error).__name__}
    output = result.stdout.strip()
    facts = []
    for line in output.splitlines():
        parts = line.strip().split('|')
        if len(parts) >= 2 and parts[0] in ('excel', 'wps-registry', 'libreoffice-registry', 'file'):
            facts.append({'kind': parts[0], 'path': parts[1], 'version': parts[2] if len(parts) > 2 else ''})
    excel = next((fact for fact in facts if fact['kind'] == 'excel'), None)
    wps_file = next((fact for fact in facts if fact['kind'] == 'file' and 'WPS' in fact['path']), None)
    libre_file = next((fact for fact in facts if fact['kind'] == 'file' and 'LibreOffice' in fact['path']), None)
    return {**base, 'probe': 'RUN' if result.returncode == 0 else 'NOT_RUN',
            'status': 'PROBED' if result.returncode == 0 else 'UNREACHABLE',
            'reachable': result.returncode == 0,
            'exit_code': result.returncode, 'facts': facts,
            'excel': excel, 'wps_usable': wps_file is not None, 'libreoffice_usable': libre_file is not None,
            'libreoffice_registry_remnant': any(fact['kind'] == 'libreoffice-registry' for fact in facts)
            and libre_file is None}


def viewing_state(macos_software, windows_software):
    """The honest preparation state for the R11 real-viewing gate.

    This is a preparation fact, never a viewing pass: ``READY_FOR_R11_REAL_VIEW``
    only means the discovered software exists for the mandatory real viewing.
    An unconfigured Windows host is ``NOT_CONFIGURED`` - it is never probed and
    never reported as a software pass.
    """
    windows = windows_software or {}
    reasons = []
    if not windows.get('configured'):
        reasons.append('windows_host_not_configured')
    else:
        if not windows.get('reachable'):
            reasons.append('windows_host_unreachable')
        if not (windows.get('excel') or windows.get('wps_usable') or windows.get('libreoffice_usable')):
            reasons.append('no_windows_spreadsheet_application_found')
    if not (macos_software or {}).get('applications'):
        reasons.append('no_macos_spreadsheet_application_found')
    if not reasons:
        return 'READY_FOR_R11_REAL_VIEW', reasons
    if 'windows_host_not_configured' in reasons:
        return 'NOT_CONFIGURED', reasons
    return 'BLOCKED', reasons


def r11_plan(windows_software, windows_root=''):
    """The exact real-viewing plan for R11; a plan, never a pass.

    An actionable Windows staging root is only recorded when
    ``GEP_TEST_WINDOWS_ROOT`` explicitly names the authorised acceptance base;
    no default user workspace is assumed.
    """
    excel = (windows_software or {}).get('excel') or {}
    root = (windows_root or '').strip().rstrip('\\')
    if root:
        windows_new_root = root + r'\<unique UTC stamp>'
        staging_status = 'CONFIGURED'
        staging = ('R11 copies the verified synthetic participants.csv/events.csv into the new unique '
                   'Windows acceptance root; the evidence root of this run names the exact local files.')
    else:
        windows_new_root = ''
        staging_status = 'NOT_CONFIGURED'
        staging = (f'No Windows staging root is recorded: set {WINDOWS_ROOT_ENV} to the authorised '
                   'Windows acceptance base before R11; no default user workspace is assumed.')
    return {
        'windows_new_root': windows_new_root,
        'windows_staging_status': staging_status,
        'staging': staging,
        'excel_path': excel.get('path') or '',
        'excel_version': excel.get('version') or '',
        'steps': [
            'Stage the two CSVs under the new Windows acceptance root (never overwrite an existing run).',
            'Open participants.csv in Excel with Data > From Text/CSV (UTF-8) and "Text" column type, not double-click.',
            'Confirm text:001 and text:=1+1 appear literally (no formula evaluation, no lost leading zero).',
            'Confirm the title cell keeps its embedded newline and the null roster code is an empty cell.',
            'Open events.csv the same way and confirm the large JSON cell is not truncated.',
            'Record the Excel version, screenshots and the observed values as R11 evidence; the human test keeps its own record.',
        ],
        'checks': ['text:001 literal', 'text:=1+1 literal', 'embedded newline inside one cell',
                   'null code empty cell', 'large cell not truncated', 'no scoring column', 'technical status enum'],
        'human_acceptance': 'NOT_RUN',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-preparation', action='store_true',
                        help='validate the CSV pair, probe installed software and record the R11 plan')
    parser.add_argument('--zip', default=None,
                        help='also validate one real browser-downloaded ZIP (exactly the two CSV members)')
    parser.add_argument('--real-import', action='store_true',
                        help='run installed software on the CSV pair when a scriptable headless application exists')
    parser.add_argument('--evidence-dir', default=None,
                        help='brand-new evidence root inside local_data/phase03_remediation_20260923 '
                             '(must not exist; symlinked path components are refused)')
    args = parser.parse_args(argv)
    if not (args.verify_preparation or args.real_import):
        parser.error('--verify-preparation or --real-import is required')

    root = evidence_root(args.evidence_dir or os.environ.get('GEP_EVIDENCE_DIR') or None)
    data_dir = root / 'db'
    data_dir.mkdir()
    setup_django(data_dir)

    import core.models as models
    from core import exports

    report = Report()
    owner, study = build_synthetic_world(models)
    item = exports.create_v2_export(owner, study.id, view='identified', language='zh')
    participants_bytes, events_bytes = exports.render_csv_bundle(item.snapshot)
    csv_dir = root / 'csv'
    csv_dir.mkdir()
    (csv_dir / 'participants.csv').write_bytes(participants_bytes)
    (csv_dir / 'events.csv').write_bytes(events_bytes)
    participant_ids = {str(person.id) for person in models.Participant.objects.filter(study=study)}
    pair_report = validate_pair(participants_bytes, events_bytes, participant_ids=participant_ids)
    report.checks.extend(pair_report.checks)

    zip_report = None
    if args.zip:
        zip_report = validate_zip(Path(args.zip), participant_ids=participant_ids)
        report.checks.extend({'name': 'zip:' + row['name'], 'ok': row['ok'], 'detail': row['detail']}
                             for row in zip_report.checks)

    config = windows_config()
    macos = probe_macos()
    windows = probe_windows(config)
    report.check('macos_software_probed', True, [app['name'] for app in macos['applications']])
    report.check('windows_probe_recorded', True, {'configured': windows.get('configured'),
                                                  'status': windows.get('status'),
                                                  'probe': windows.get('probe'),
                                                  'reachable': windows.get('reachable'),
                                                  'error': windows.get('error'),
                                                  'excel': bool(windows.get('excel'))})
    # Availability of the real spreadsheet application is a fact for the R11
    # gate, never a pass: READY means "software exists for the mandatory real
    # viewing", BLOCKED/NOT_CONFIGURED means the R11 gate has to wait or record
    # the external dependency. Either way the rest of the engineering work is
    # not blocked.
    viewing_status, viewing_reasons = viewing_state(macos, windows)
    report.check('viewing_status_recorded', True, {'status': viewing_status, 'reasons': viewing_reasons})

    import_result = None
    if args.real_import:
        # Only the real installed application is driven; a stray ``soffice`` on
        # PATH (for example a bundled runtime of another tool) is never treated
        # as the user's spreadsheet application.
        installed = Path('/Applications/LibreOffice.app/Contents/MacOS/soffice')
        soffice = str(installed) if installed.is_file() else ''
        if soffice:
            converted = root / 'converted'
            converted.mkdir()
            result = subprocess.run([soffice, '--headless', '--convert-to', 'xlsx', '--outdir', str(converted),
                                     str(csv_dir / 'participants.csv')], capture_output=True, text=True, timeout=120)
            import_result = {'tool': 'libreoffice_headless', 'path': soffice, 'exit_code': result.returncode,
                             'converted': (converted / 'participants.xlsx').is_file()}
            report.check('real_import_completed', result.returncode == 0 and import_result['converted'], import_result)
        else:
            # No scriptable headless application: the real on-screen viewing of
            # the installed Excel stays the R11 gate, and this is never a pass.
            import_result = {'tool': None, 'status': 'BLOCKED',
                             'reason': 'no scriptable headless spreadsheet application found; '
                                       'installed Excel is driven at the R11 gate'}
            report.check('real_import_completed', False, import_result)

    guide = root / 'r11_viewing_plan.md'
    plan = r11_plan(windows, config['windows_root'])
    windows_root_line = plan['windows_new_root'] or f'NOT_CONFIGURED (set {WINDOWS_ROOT_ENV})'
    guide.write_text(
        '# R11 spreadsheet viewing plan (P03R08 preparation)\n\n'
        f'Windows new-product root: `{windows_root_line}`\n\n'
        f'Excel: `{plan["excel_path"]}` version `{plan["excel_version"]}`\n\n'
        + '\n'.join(f'- {step}' for step in plan['steps']) + '\n\n'
        'Checks: ' + ', '.join(plan['checks']) + '\n\n'
        'Status: preparation only. The real on-screen viewing is an R11 gate and a human test keeps its own record.\n',
        encoding='utf-8')
    report.check('viewing_plan_written', guide.is_file())

    document = {
        'task': 'p03r08p', 'tool': 'remediation_spreadsheet.py',
        'generated_at': datetime.now(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'host_platform': sys.platform,
        'windows_config': {'host_configured': bool(config['host']),
                           'host_key_alias_configured': bool(config['host_key_alias']),
                           'windows_root_configured': bool(config['windows_root'])},
        'csv_files': {
            'participants': {'path': str(csv_dir / 'participants.csv'), 'bytes': len(participants_bytes),
                             'sha256': sha256_bytes(participants_bytes)},
            'events': {'path': str(csv_dir / 'events.csv'), 'bytes': len(events_bytes),
                       'sha256': sha256_bytes(events_bytes)}},
        'software': {'macos': macos, 'windows': windows},
        'viewing_status': viewing_status, 'viewing_reasons': viewing_reasons,
        'preparation_status': 'PREPARED' if report.ok else 'FAILED',
        'human_acceptance': 'NOT_RUN',
        'r11_plan': plan,
        'real_import': import_result,
        'checks': report.checks, 'result': 'PASS' if report.ok else 'FAIL',
        'failures': report.failures(),
    }
    (root / 'spreadsheet_preparation.json').write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'result': document['result'], 'preparation_status': document['preparation_status'],
                      'viewing_status': viewing_status,
                      'windows_status': windows.get('status'), 'windows_probe': windows.get('probe'),
                      'macos_applications': [app['name'] + ' ' + app['version'] for app in macos['applications']],
                      'windows_excel': (windows.get('excel') or {}).get('version', ''),
                      'windows_wps': windows.get('wps_usable'), 'windows_libreoffice': windows.get('libreoffice_usable'),
                      'csv_bytes': {'participants': len(participants_bytes), 'events': len(events_bytes)},
                      'failed_checks': report.failures(), 'evidence': str(root / 'spreadsheet_preparation.json')},
                     ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == '__main__':
    sys.exit(main())
