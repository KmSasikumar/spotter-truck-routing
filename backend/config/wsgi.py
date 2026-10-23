"""WSGI entrypoint for production servers (gunicorn).

Run with: gunicorn config.wsgi:application --chdir backend
(Render passes $PORT; the Procfile/start command binds 0.0.0.0:$PORT.)
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
