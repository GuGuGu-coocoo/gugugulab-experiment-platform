import uuid
from django.db import models
from django.conf import settings

class Identified(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    class Meta:
        abstract = True

class Instance(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    instance_id = models.UUIDField(unique=True)
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

class Study(Identified):
    title = models.CharField(max_length=160)
    mode = models.CharField(max_length=16, default='anonymous')
    recruitment = models.CharField(max_length=16, default='paused')
    max_sessions = models.PositiveIntegerField(default=1)

class Grant(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    study = models.ForeignKey(Study, on_delete=models.CASCADE)
    action = models.CharField(max_length=48)
    delegable = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user','study','action'], name='grant_unique')]

class Build(Identified):
    study = models.ForeignKey(Study, on_delete=models.PROTECT)
    descriptor = models.JSONField()
    digest = models.CharField(max_length=64)
    package_path = models.CharField(max_length=256, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['study','digest'], name='build_digest')]

class Release(Identified):
    study = models.ForeignKey(Study, on_delete=models.PROTECT)
    build = models.ForeignKey(Build, on_delete=models.PROTECT)
    config = models.JSONField()
    approved = models.BooleanField(default=False)

class Participant(Identified):
    study = models.ForeignKey(Study, on_delete=models.PROTECT)
    code = models.CharField(max_length=128, null=True)
    password_hash = models.CharField(max_length=256, blank=True)
    active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['study','code'], name='study_code')]

class Session(Identified):
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT)
    release = models.ForeignKey(Release, on_delete=models.PROTECT)
    operation = models.UUIDField(unique=True)
    proof_hash = models.CharField(max_length=64)
    request = models.JSONField()
    token_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False)
    completion = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Event(models.Model):
    session = models.ForeignKey(Session, on_delete=models.PROTECT)
    event_id = models.UUIDField()
    segment_id = models.UUIDField()
    sequence = models.PositiveIntegerField()
    envelope = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['session','event_id'], name='event_identity'), models.UniqueConstraint(fields=['session','segment_id','sequence'], name='segment_sequence')]

class Export(Identified):
    study = models.ForeignKey(Study, on_delete=models.PROTECT)
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

class Audit(models.Model):
    study = models.ForeignKey(Study, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=48)
    target = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

class Invitation(Identified):
    study = models.ForeignKey(Study, on_delete=models.PROTECT)
    issuer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    token_hash = models.CharField(max_length=64, unique=True)
    username = models.CharField(max_length=150)
    actions = models.JSONField()
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
    revoked = models.BooleanField(default=False)

class RecoveryPermit(Identified):
    session = models.ForeignKey(Session, on_delete=models.PROTECT)
    token_hash = models.CharField(max_length=64,unique=True)
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
    issuer = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)

class Throttle(models.Model):
    key = models.CharField(max_length=64,primary_key=True)
    window = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)
