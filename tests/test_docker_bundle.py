"""Bundle metadata/selection regressions, not a substitute for real Docker runs."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('docker_release_under_test', ROOT/'tools/docker_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    # Minimal synthetic guide tree; no real images, data or Docker daemon.
    files = ('compose.yaml','compose.public.yaml','.env.example','deploy/windows.ps1',
             'deploy/start_windows.cmd','deploy/linux.sh','deploy/Caddyfile','deploy/README.md','LICENSE','README.md','README_ZH.md',
             'third_party/godot_sqlite_license.md','CHANGELOG.md')
    guides = ('deployment.md','deployment_en.md','researcher.md','researcher_en.md',
              'gec_godot.md','gec_godot_en.md','moss_duel_gec.md','moss_duel_gec_en.md',
              'protocol.md','security.md','SECURITY_AUDIT.md','security_component_review.md',
              'upgrade_0_1_1.md','upgrade_0_1_1_en.md','release_notes_0_1_1.md')
    for name in (*files, *('docs/'+name for name in guides)):
        path = tmp_path/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic guide', encoding='utf-8')
    (tmp_path/'compose.public.yaml').write_text('image: ${GEP_CADDY_IMAGE:-gep-caddy:2.11.7-go1.26.10}')
    (tmp_path/'pyproject.toml').write_text('[project]\nversion = "0.1.1"\n')
    (tmp_path/'GEC_SDK').mkdir()
    (tmp_path/'README.md').write_text('[SDK](GEC_SDK/)')
    monkeypatch.setattr(release, 'copy_guide_images', lambda output: None)
    details = {'app': {'Id':'sha256:synthetic-app','Os':'linux','Architecture':'amd64'},
               'proxy': {'Id':'sha256:synthetic-proxy','Os':'linux','Architecture':'amd64'}}
    calls=[]
    def inspect(command, **kwargs):
        assert command[:3] == ['docker','image','inspect']
        return json.dumps([details[command[3]]])
    def save(command, **kwargs):
        calls.append(command)
        assert command[:4] == ['docker','image','save','--output']
        Path(command[4]).write_bytes(b'synthetic image archive for selection test only')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(release.subprocess, 'check_output', inspect)
    monkeypatch.setattr(release.subprocess, 'run', save)
    monkeypatch.setattr('sys.argv', ['docker_release','--image','app','--caddy-image','proxy',
                                   '--output',str(tmp_path/'build/candidate')])
    return tmp_path,details,calls


def test_archive_selects_both_images_and_records_actual_identity(bundle):
    root,_,calls=bundle
    release.main()
    assert calls[0][-2:] == ['app','proxy']
    metadata=json.loads((root/'build/candidate/docker-release.json').read_text())
    assert metadata['image_id']=='sha256:synthetic-app'
    assert metadata['version']=='0.1.1'
    assert metadata['caddy_image']=='proxy' and metadata['caddy_image_id']=='sha256:synthetic-proxy'
    assert '${GEP_CADDY_IMAGE:-proxy}' in (root/'build/candidate/compose.public.yaml').read_text()
    with zipfile.ZipFile(root/'build/candidate.zip') as z:
        assert 'candidate/gep-image.tar' in z.namelist()
        assert 'candidate/docs/upgrade_0_1_1.md' in z.namelist()
        assert 'candidate/docs/upgrade_0_1_1_en.md' in z.namelist()
        assert '/tree/v0.1.1/GEC_SDK' in z.read('candidate/README.md').decode()
        assert not any('/data/' in name or '/internal/' in name for name in z.namelist())


@pytest.mark.parametrize('change',[{'Architecture':'arm64'},{'Os':'windows'}])
def test_wrong_platform_proxy_refuses_before_creating_delivery(bundle, change):
    root,details,calls=bundle
    details['proxy'].update(change)
    with pytest.raises(SystemExit): release.main()
    assert not (root/'build/candidate').exists() and not calls


def test_non_linux_application_refuses_before_creating_delivery(bundle):
    root,details,calls=bundle
    details['app']['Os']='windows'
    with pytest.raises(SystemExit): release.main()
    assert not (root/'build/candidate').exists() and not calls
