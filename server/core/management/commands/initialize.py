import getpass
import os
import secrets
import uuid
from django.core.management import BaseCommand, CommandError, call_command
from django.conf import settings
from django.db import transaction
from django.contrib.auth import get_user_model
from core.models import Instance

class Command(BaseCommand):
    help='Initialize a new empty synthetic instance; never repairs or overwrites a volume.'
    def add_arguments(self,parser):
        parser.add_argument('--username',required=True)
    def handle(self,*args,**options):
        root=settings.DATA_DIR
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if any(root.iterdir()):
            raise CommandError('Data directory must be empty; existing volume is never reinitialized.')
        password=getpass.getpass('New synthetic Owner password: ')
        if len(password)<16:
            raise CommandError('Use at least 16 characters.')
        secret=secrets.token_urlsafe(48)
        instance_id=uuid.uuid4()
        call_command('migrate',verbosity=0)
        with transaction.atomic():
            owner=get_user_model().objects.create_user(options['username'],password=password)
            Instance.objects.create(instance_id=instance_id,owner=owner)
        for name,value in [('secret',secret),('instance',str(instance_id))]:
            fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as stream:
                stream.write(value)
                stream.flush();os.fsync(stream.fileno())
        self.stdout.write('Synthetic instance initialized. Record the instance marker in deployment configuration.')
