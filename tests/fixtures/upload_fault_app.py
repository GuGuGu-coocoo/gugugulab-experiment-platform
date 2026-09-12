"""Test-only WSGI wrapper: pause at a real package durability boundary."""
import os
from pathlib import Path
import time
from gep.wsgi import application


def pause():
    Path(os.environ['GEP_UPLOAD_FAULT_MARKER']).write_text('package boundary reached')
    time.sleep(30)


mode = os.environ.get('GEP_UPLOAD_FAULT')
if mode == 'before_rename':
    original_fsync = os.fsync
    def fsync(fd):
        original_fsync(fd)
        pause()
    os.fsync = fsync
elif mode == 'after_rename':
    original_replace = os.replace
    def replace(source, target, *args, **kwargs):
        result = original_replace(source, target, *args, **kwargs)
        if str(source).endswith('.tmp') and str(target).endswith('.zip'):
            pause()
        return result
    os.replace = replace
