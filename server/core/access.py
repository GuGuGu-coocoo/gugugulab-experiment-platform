from .models import AccountProfile, Grant, Instance, Study
from .protocol import require

ACTIONS = {'study.view','study.configure','build.upload','build.preview','release.approve_pilot','recruitment.manage','data.export_raw','session.recover','member.manage','permission.delegate','audit.view','identity_mapping.read','session.view'}
ROLES = {'admin','user'}
# New in 03B: explicit identity/session visibility actions. They are never
# backfilled into existing grants; only explicit assignment (or a newly created
# study for its creator) can add them.
NEW_ACTIONS = {'identity_mapping.read','session.view'}


def profile_of(user):
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        return None
    return AccountProfile.objects.filter(user_id=user.pk).first()


def effective_role(user):
    """Fail closed/safe ordinary: a missing or unknown profile never grants Admin."""
    profile = profile_of(user)
    return profile.role if profile is not None and profile.role in ROLES else 'user'


def is_instance_owner(user):
    """Owner derives only from Instance.owner, never from is_staff/is_superuser/Grant."""
    return bool(getattr(user, 'is_authenticated', False) and getattr(user, 'pk', None) and Instance.objects.filter(owner_id=user.pk).exists())


def is_account_administrator(user):
    return is_instance_owner(user) or effective_role(user) == 'admin'


def actions_for(user, study):
    return set(Grant.objects.filter(user=user, study=study).values_list('action', flat=True))


def allowed(user, study, action):
    """Ordinary study access requires study.view plus the explicit action."""
    if not (getattr(user, 'is_authenticated', False) and user.is_active):
        return False
    if action not in ACTIONS:
        return False
    actions = actions_for(user, study)
    if not actions:
        return False
    if action == 'study.view':
        return 'study.view' in actions
    return action in actions and 'study.view' in actions


def delegable_actions(user, study=None):
    grants = Grant.objects.filter(user=user, delegable=True)
    if study is not None:
        grants = grants.filter(study=study)
    return set(grants.values_list('action', flat=True))


def target_privileges(user):
    """Every study-scoped privilege the target holds, including nondelegable grants."""
    return set(Grant.objects.filter(user=user).values_list('study_id', 'action'))


def delegable_authority(user):
    """Study-scoped authority the actor may delegate: delegable grants whose
    study.view prerequisite holds on the same study."""
    grants = set(Grant.objects.filter(user=user, delegable=True).values_list('study_id', 'action'))
    visible = {study_id for study_id, action in grants if action == 'study.view'}
    return {(study_id, action) for study_id, action in grants if action == 'study.view' or study_id in visible}


def dominates(actor, target):
    """Takeover guard: study-scoped (study_id, action) authority comparison.

    The actor must be authorized on the same study and able to delegate every
    target privilege whose control would be acquired, including target grants
    that are nondelegable. The instance Owner bypasses the comparison; a missing
    profile or instance role never contributes study authority.
    """
    if is_instance_owner(actor):
        return True
    return delegable_authority(actor) >= target_privileges(target)


def conflicts():
    """(user_id, study_id, conflicting_actions) for grants that lack study.view."""
    grouped = {}
    for user_id, study_id, action in Grant.objects.values_list('user_id', 'study_id', 'action'):
        grouped.setdefault((user_id, study_id), set()).add(action)
    return sorted((user_id, study_id, sorted(actions - {'study.view'})) for (user_id, study_id), actions in grouped.items() if actions - {'study.view'} and 'study.view' not in actions)


def authority_actions(user, study):
    """Actions the actor may assign on this study: effective AND delegable.

    The same explicit-action and visibility prerequisites that govern use also
    govern delegation, so an Admin can never grant more than they hold.
    """
    return {action for study_id, action in delegable_authority(user) if study_id == study.pk}


def manageable_actions(actor, study):
    """Owner may assign any catalog action; Admin is limited to delegable_authority."""
    if is_instance_owner(actor):
        return set(ACTIONS)
    return authority_actions(actor, study)


def visible_studies(user):
    """Studies where the user holds at least one explicit grant (matrix scope)."""
    return Study.objects.filter(grant__user=user).distinct()


def guard(user, study, action):
    require(allowed(user, study, action), 'forbidden', 403)
