from drf_spectacular.extensions import OpenApiAuthenticationExtension


class CallbackAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "auditor_bridge.authentication.CallbackAuthentication"
    name = "auditorCallbackBearer"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "description": "Dedicated Auditor callback credential"}
