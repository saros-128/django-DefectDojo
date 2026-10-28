"""Opt-in wrapper: load upstream and operator settings before adding bridge routes."""
import os

from dojo.settings.settings import *  # noqa: F403

if os.environ.get("AUDITOR_BRIDGE_ENABLED", "false").lower() == "true":
    INSTALLED_APPS = (*INSTALLED_APPS, "auditor_bridge")  # noqa: F405
    AUDITOR_BRIDGE_BASE_URLCONF = ROOT_URLCONF  # noqa: F405
    ROOT_URLCONF = "auditor_bridge.urls"
