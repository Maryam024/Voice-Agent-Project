import os

import socketio
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_app = get_asgi_application()

from frontdesk.realtime import sio  # noqa: E402 (needs Django to be ready first)

# Socket.IO answers on /socket.io, everything else goes to Django as before.
application = socketio.ASGIApp(sio, django_app)