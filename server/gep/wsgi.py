from .runtime import configure, verify
configure()
from django.core.wsgi import get_wsgi_application
application=get_wsgi_application()
verify()
