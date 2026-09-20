"""Participant-facing site: public portal, stable study entry and the www link page.

Host responsibilities (2026-09-20 decision, local implementation only):

* ``www`` is the personal site and only links to the experiment portal;
* ``experiment`` hosts the portal, the stable study entry, experiment resources
  and the GEC data API;
* the admin workbench stays on its own host with a host-only cookie.

The portal lists only explicitly public studies that are open and have a valid
current release; closed studies are only shown as an opt-in summary without any
start. The stable entry page never renders roster, session or history fields.
"""
from django.conf import settings
from django.shortcuts import render

from . import publication
from .gui import public_api_url
from .models import Study
from .protocol import require
from .views import endpoint

EXPERIMENT_LOCAL_HOSTS = ('127.0.0.1',)


def experiment_host(request):
    """Experiment origin check; 127.0.0.1 stays available for local acceptance."""
    require(request.get_host().split(':')[0] in (settings.EXPERIMENT_HOST, *EXPERIMENT_LOCAL_HOSTS), 'wrong_host', 403)


def entry_url(study):
    return f'{public_api_url()}/join/{study.id}'


def start_url(release):
    return f'{public_api_url()}/run/{release.id}/web/index.html'


def gated_start_url(release, revision):
    """The stable entry carries the release/revision it observed into the app URL.

    The resource view validates the binding before serving the application, and
    the injected context keeps the binding for the atomic admission check, so a
    stale entry page can never silently start the superseded materials.
    """
    return f'{start_url(release)}?entry_release={release.id}&entry_revision={int(revision)}'


def recruiting_studies():
    """Explicit public + open studies whose current release is approved and located."""
    listed = []
    for study in Study.objects.filter(public=True, recruitment='open').select_related('current_release__build').order_by('title', 'id'):
        if publication.release_available(study.current_release):
            listed.append(study)
    return listed


def closed_summaries():
    """Explicit public + closed studies that opted in to an ended summary."""
    return list(Study.objects.filter(public=True, recruitment='closed', show_closed_summary=True).order_by('title', 'id'))


def _portal(request):
    require(request.method == 'GET', 'method', 405)
    entries = [{'study': study, 'snapshot': publication.public_snapshot(study), 'entry_url': entry_url(study),
                'start_url': start_url(study.current_release), 'revision': study.revision}
               for study in recruiting_studies()]
    closed = [{'study': study, 'snapshot': publication.public_snapshot(study)} for study in closed_summaries()]
    return render(request, 'core/portal.html', {'entries': entries, 'closed': closed, 'portal_title': 'GuGuGu Lab · 参与研究'})


def _www(request):
    require(request.method == 'GET', 'method', 405)
    return render(request, 'core/www.html', {'portal_url': public_api_url() + '/'})


@endpoint
def site_root(request):
    """Route the root by host so admin, portal and the www link page never mix."""
    host = request.get_host().split(':')[0]
    if host == settings.WWW_HOST:
        return _www(request)
    if host in (settings.EXPERIMENT_HOST, *EXPERIMENT_LOCAL_HOSTS):
        return _portal(request)
    from .gui import home
    return home(request)


@endpoint
def entry(request, study_id):
    """Stable study-level participant entry; new sessions bind the current release."""
    experiment_host(request)
    require(request.method == 'GET', 'method', 405)
    study = Study.objects.select_related('current_release__build').filter(pk=study_id).first()
    require(study is not None, 'study_not_found', 404)
    release = study.current_release if study.recruitment == 'open' else None
    startable = study.recruitment == 'open' and publication.release_available(release)
    visible_summary = study.public_summary if study.public and (study.recruitment == 'open' or study.show_closed_summary) else ''
    response = render(request, 'core/entry.html', {
        'study': study, 'title': study.title, 'summary': visible_summary,
        'duration': study.public_duration if study.public else '',
        'device_requirements': study.public_device_requirements if study.public else '',
        'startable': startable,
        'start_url': start_url(release) if startable else '',
        'gated_start_url': gated_start_url(release, study.revision) if startable else '',
        'expected_release': str(release.id) if startable else '',
        'expected_revision': study.revision,
        'closed': study.recruitment == 'closed',
        'paused': study.recruitment == 'paused',
        'has_current': study.current_release_id is not None,
    })
    response['Cache-Control'] = 'no-store'
    return response
