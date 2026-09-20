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
    governance_revision = models.PositiveIntegerField(default=0)

class AccountProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='gep_profile')
    role = models.CharField(max_length=16, default='user')
    must_change_password = models.BooleanField(default=False)
    auth_version = models.PositiveIntegerField(default=1)
    revision = models.PositiveIntegerField(default=0)

class AccountInvitation(Identified):
    issuer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    token_hash = models.CharField(max_length=64, unique=True)
    username = models.CharField(max_length=150)
    role = models.CharField(max_length=16, default='user')
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
    revoked = models.BooleanField(default=False)

class Study(Identified):
    title = models.CharField(max_length=160)
    mode = models.CharField(max_length=16, default='anonymous')
    recruitment = models.CharField(max_length=16, default='paused')
    max_sessions = models.PositiveIntegerField(default=1)
    # Explicit publication policy. Public listing is a separate researcher
    # decision, never inferred from mode or recruitment, and the current release
    # is nullable on purpose: legacy studies get no guessed release.
    public = models.BooleanField(default=False)
    public_summary = models.CharField(max_length=280, blank=True)
    public_duration = models.CharField(max_length=80, blank=True)
    public_device_requirements = models.CharField(max_length=160, blank=True)
    show_closed_summary = models.BooleanField(default=False)
    current_release = models.ForeignKey('Release', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    # Publication revision: every explicit policy or current-release change bumps
    # it, and a submission carrying a stale revision is refused.
    revision = models.PositiveIntegerField(default=0)

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
    # Complete immutable distribution artifact (03D). The outer digest of the
    # finished package lives here only; the artifact's own manifest lists member
    # hashes and never the artifact digest, so nothing references itself.
    artifact_path = models.CharField(max_length=256, blank=True)
    artifact_digest = models.CharField(max_length=64, blank=True)
    artifact_size = models.PositiveBigIntegerField(default=0)

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
    study = models.ForeignKey(Study, on_delete=models.PROTECT, null=True, blank=True)
    # Device-initiated recovery carries no human actor; every governance and
    # administrative action still names one.
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    action = models.CharField(max_length=48)
    target = models.CharField(max_length=128)
    before = models.JSONField(null=True)
    after = models.JSONField(null=True)
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

class RecoveryCode(Identified):
    """Versioned six-digit one-time recovery ticket bound to one session.

    The digits themselves are never stored: ``code_hash`` is an HMAC of the
    submitted code under the server secret, so a database or log reader cannot
    enumerate the million possible codes. The row binds the session, its study,
    its frozen release and the issuing researcher; redemption additionally
    requires the original device proof, the same binding, a live issuer
    authorization and the five-attempt/five-minute limits. ``superseded`` marks
    an earlier ticket invalidated by a newer issuance for the same session.
    """
    session = models.ForeignKey(Session, on_delete=models.PROTECT)
    study = models.ForeignKey(Study, on_delete=models.PROTECT, related_name='+')
    release = models.ForeignKey(Release, on_delete=models.PROTECT, related_name='+')
    issuer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
    superseded = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Throttle(models.Model):
    key = models.CharField(max_length=64,primary_key=True)
    window = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)

class PermissionPreview(models.Model):
    """Single-use, expiring commit identity for preview-bound governance changes.

    ``summary`` is the redacted preview, ``staged`` holds the private normalized
    intent (roster password hashes stay here only) and ``binding`` is the digest
    of intended operations plus the observed target state, so a commit is
    refused with zero partial mutations when anything moved after the preview.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=24)
    scope = models.CharField(max_length=64, blank=True)
    summary = models.JSONField(default=dict)
    errors = models.JSONField(default=list)
    binding = models.CharField(max_length=64)
    base_revision = models.PositiveIntegerField(default=0)
    staged = models.JSONField(null=True, blank=True)
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
    result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
