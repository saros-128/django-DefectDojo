from django.apps import AppConfig


class AuditorBridgeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "auditor_bridge"

    def ready(self):
        import auditor_bridge.schema  # noqa: PLC0415, F401 -- register authentication schema
