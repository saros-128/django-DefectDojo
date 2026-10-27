import hmac
import os

from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from auditor_bridge.config import secret


class CallbackAuthentication(BaseAuthentication):
    def authenticate(self, request):
        expected = "Bearer " + secret("AUDITOR_BRIDGE_CALLBACK_TOKEN")
        if not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
            msg = "Invalid callback credential"
            raise AuthenticationFailed(msg)
        user = get_user_model().objects.filter(username=os.environ["AUDITOR_BRIDGE_IMPORT_USER"],
                                              is_active=True, is_superuser=True).first()
        if user is None:
            msg = "Callback service identity is unavailable"
            raise AuthenticationFailed(msg)
        return user, "auditor_callback"

    def authenticate_header(self, request):
        return "Bearer"
