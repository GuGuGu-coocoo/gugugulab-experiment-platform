"""P03R08P: the preparation tool's Windows settings come from the runtime
environment only, and a missing setting never turns into network access.

These tests pin the portability contract of ``tools/remediation_spreadsheet.py``
after the R08 review found hardcoded machine-specific values:

* no/blank ``GEP_TEST_SSH_HOST`` -> no subprocess at all,
  ``NOT_CONFIGURED``/``NOT_RUN``, and the local file preparation stays
  independent;
* a configured host runs the fixed read-only probe as an argv list with
  ``StrictHostKeyChecking=yes`` and ``BatchMode=yes`` (never ``no``/
  ``accept-new``, never a known-hosts override), adding ``HostKeyAlias`` only
  when it is explicitly configured;
* supplied values are argv elements, never shell-interpolated;
* an actionable Windows staging plan needs an explicit ``GEP_TEST_WINDOWS_ROOT``
  - no default user workspace is invented;
* software discovery is a preparation fact, never a viewing pass.
"""
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import remediation_spreadsheet as tool  # noqa: E402  (tools/ path above)

SETTING_ENVS = (tool.SSH_HOST_ENV, tool.SSH_HOST_KEY_ALIAS_ENV, tool.WINDOWS_ROOT_ENV)
EXCEL_PATH = r'C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE'
EXCEL_VERSION = '16.0.17932.20910'


def clear_settings(monkeypatch):
    for name in SETTING_ENVS:
        monkeypatch.delenv(name, raising=False)


def capture_run(monkeypatch, stdout='', returncode=0):
    """Replace subprocess.run and capture the exact argv and kwargs."""
    captured = {}

    def fake_run(command, **kwargs):
        captured['command'] = command
        captured['kwargs'] = kwargs
        return subprocess.CompletedProcess(command, returncode, stdout, '')

    monkeypatch.setattr(tool.subprocess, 'run', fake_run)
    return captured


def test_missing_or_blank_settings_never_touch_the_network(monkeypatch):
    """No configured host (missing or blank) means no subprocess and an honest
    ``NOT_CONFIGURED``/``NOT_RUN`` record, never a Windows software pass."""
    clear_settings(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError(f'network access attempted without configuration: {args!r}')

    monkeypatch.setattr(tool.subprocess, 'run', forbidden)
    windows = tool.probe_windows()
    assert windows['configured'] is False
    assert windows['status'] == 'NOT_CONFIGURED' and windows['probe'] == 'NOT_RUN'
    assert windows['reachable'] is None and windows['facts'] == [] and windows['excel'] is None
    assert windows['wps_usable'] is False and windows['libreoffice_usable'] is False

    # A blank value is not a configured value either.
    monkeypatch.setenv(tool.SSH_HOST_ENV, '   ')
    monkeypatch.setenv(tool.SSH_HOST_KEY_ALIAS_ENV, '')
    assert tool.probe_windows()['status'] == 'NOT_CONFIGURED'
    assert tool.windows_config()['host'] == ''

    # The local preparation is independent: the missing host is a recorded
    # reason, not a pass and not a crash.
    status, reasons = tool.viewing_state({'applications': [{'name': 'Microsoft Excel'}]}, windows)
    assert status == 'NOT_CONFIGURED'
    assert 'windows_host_not_configured' in reasons
    assert status != 'READY_FOR_R11_REAL_VIEW'


def test_configured_host_runs_the_read_only_probe_with_strict_host_key_checking(monkeypatch):
    """A configured host runs the fixed probe through an argv list that always
    keeps strict host-key checking; only an explicit alias pins HostKeyAlias."""
    monkeypatch.setenv(tool.SSH_HOST_ENV, 'test-windows-host')
    monkeypatch.setenv(tool.SSH_HOST_KEY_ALIAS_ENV, 'test-host-key-alias')
    monkeypatch.delenv(tool.WINDOWS_ROOT_ENV, raising=False)
    captured = capture_run(monkeypatch, stdout=f'excel|{EXCEL_PATH}|{EXCEL_VERSION}\n')

    windows = tool.probe_windows()
    command = captured['command']
    assert isinstance(command, list) and command[0] == 'ssh'
    assert 'test-windows-host' in command
    assert command[-1].startswith('powershell -NoProfile -EncodedCommand ')
    assert 'BatchMode=yes' in command and 'ConnectTimeout=8' in command
    assert command.count('StrictHostKeyChecking=yes') == 1
    assert not any('StrictHostKeyChecking=no' in value or 'accept-new' in value for value in command)
    assert not any('KnownHostsFile' in value for value in command)
    assert 'HostKeyAlias=test-host-key-alias' in command
    assert captured['kwargs'].get('shell', False) is False
    assert captured['kwargs'].get('timeout') == 45

    assert windows['configured'] is True and windows['status'] == 'PROBED' and windows['probe'] == 'RUN'
    assert windows['reachable'] is True and windows['exit_code'] == 0
    assert windows['excel'] == {'kind': 'excel', 'path': EXCEL_PATH, 'version': EXCEL_VERSION}
    status, reasons = tool.viewing_state({'applications': [{'name': 'Microsoft Excel'}]}, windows)
    assert status == 'READY_FOR_R11_REAL_VIEW' and reasons == []


def test_omitted_key_alias_keeps_the_normal_known_hosts_check(monkeypatch):
    """Without GEP_TEST_SSH_HOST_KEY_ALIAS the configured name is checked
    normally; strict verification is not weakened in any way."""
    monkeypatch.setenv(tool.SSH_HOST_ENV, 'test-windows-host')
    monkeypatch.delenv(tool.SSH_HOST_KEY_ALIAS_ENV, raising=False)
    captured = capture_run(monkeypatch)

    tool.probe_windows()
    command = captured['command']
    assert 'StrictHostKeyChecking=yes' in command
    assert not any(str(value).startswith('HostKeyAlias=') for value in command)


def test_supplied_values_stay_argv_elements_and_are_never_shell_interpolated(monkeypatch):
    """Shell metacharacters in the supplied host or alias are inert: the values
    are passed as single argv elements and never through a shell."""
    hostile_host = 'host; touch /tmp/gep-p03r08p-should-not-exist'
    hostile_alias = '$(id)'
    monkeypatch.setenv(tool.SSH_HOST_ENV, hostile_host)
    monkeypatch.setenv(tool.SSH_HOST_KEY_ALIAS_ENV, hostile_alias)
    captured = capture_run(monkeypatch, returncode=255)

    windows = tool.probe_windows()
    command = captured['command']
    assert hostile_host in command
    assert f'HostKeyAlias={hostile_alias}' in command
    assert captured['kwargs'].get('shell', False) is False
    assert not Path('/tmp/gep-p03r08p-should-not-exist').exists()
    assert windows['status'] == 'UNREACHABLE' and windows['probe'] == 'NOT_RUN'
    assert windows['reachable'] is False


def test_actionable_windows_staging_plan_requires_explicit_root():
    """No GEP_TEST_WINDOWS_ROOT -> no actionable Windows staging root and no
    invented user workspace; an explicit root is the only source of the plan."""
    unconfigured = tool.r11_plan({'configured': False}, '')
    assert unconfigured['windows_new_root'] == ''
    assert unconfigured['windows_staging_status'] == 'NOT_CONFIGURED'
    assert tool.WINDOWS_ROOT_ENV in unconfigured['staging']
    assert tool.r11_plan({'configured': True}, '   ')['windows_new_root'] == ''

    configured = tool.r11_plan({'configured': True}, 'D:\\acceptance\\remediation')
    assert configured['windows_new_root'] == 'D:\\acceptance\\remediation\\<unique UTC stamp>'
    assert configured['windows_staging_status'] == 'CONFIGURED'


def test_viewing_state_is_a_preparation_fact_not_a_viewing_pass():
    """READY only means the software exists for the mandatory R11 viewing;
    unreachable or missing software stays BLOCKED."""
    macos = {'applications': [{'name': 'Microsoft Excel'}]}
    configured = {'configured': True, 'reachable': True, 'excel': {'path': EXCEL_PATH, 'version': EXCEL_VERSION},
                  'wps_usable': False, 'libreoffice_usable': False}
    assert tool.viewing_state(macos, configured)[0] == 'READY_FOR_R11_REAL_VIEW'
    unreachable = {'configured': True, 'reachable': False, 'excel': None,
                   'wps_usable': False, 'libreoffice_usable': False}
    status, reasons = tool.viewing_state(macos, unreachable)
    assert status == 'BLOCKED' and 'windows_host_unreachable' in reasons
    empty_windows = {'configured': True, 'reachable': True, 'excel': None,
                     'wps_usable': False, 'libreoffice_usable': False}
    assert tool.viewing_state(macos, empty_windows)[0] == 'BLOCKED'
    assert tool.viewing_state({'applications': []}, configured)[0] == 'BLOCKED'


def test_missing_configuration_run_still_prepares_locally(evidence_root):
    """The whole tool with no Windows settings: local synthetic CSV preparation
    passes, Windows is NOT_CONFIGURED/NOT_RUN, and the staging plan stays empty."""
    run_root = evidence_root / f'missing-config-{secrets.token_hex(4)}'
    env = {name: value for name, value in os.environ.items()
           if name not in SETTING_ENVS and name != 'GEP_EVIDENCE_DIR'}
    result = subprocess.run([sys.executable, str(TOOLS / 'remediation_spreadsheet.py'),
                             '--verify-preparation', '--evidence-dir', str(run_root)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr

    document = json.loads((run_root / 'spreadsheet_preparation.json').read_text(encoding='utf-8'))
    assert document['result'] == 'PASS' and document['preparation_status'] == 'PREPARED'
    windows = document['software']['windows']
    assert windows['configured'] is False and windows['status'] == 'NOT_CONFIGURED'
    assert windows['probe'] == 'NOT_RUN' and windows['reachable'] is None and windows['facts'] == []
    assert document['windows_config'] == {'host_configured': False, 'host_key_alias_configured': False,
                                          'windows_root_configured': False}
    assert document['viewing_status'] == 'NOT_CONFIGURED'
    assert 'windows_host_not_configured' in document['viewing_reasons']
    assert document['r11_plan']['windows_new_root'] == ''
    assert document['r11_plan']['windows_staging_status'] == 'NOT_CONFIGURED'
    assert document['human_acceptance'] == 'NOT_RUN'
    for name in ('participants', 'events'):
        entry = document['csv_files'][name]
        assert Path(entry['path']).is_file() and entry['bytes'] > 0
