from .models import Grant
from .protocol import require

ACTIONS = {'study.view','study.configure','build.upload','build.preview','release.approve_pilot','recruitment.manage','data.export_raw','session.recover','member.manage','permission.delegate','audit.view'}

def allowed(user, study, action):
    return user.is_authenticated and user.is_active and Grant.objects.filter(user=user,study=study,action=action).exists()

def guard(user, study, action):
    require(allowed(user,study,action), 'forbidden', 403)
