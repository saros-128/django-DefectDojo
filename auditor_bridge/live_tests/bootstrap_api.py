
from common import SECRETS, request, save, secret, wait_services

wait_services()
token = request("dojo", "POST", "/api/v2/api-token-auth/", body={"username": "admin", "password": secret("dojo-admin")})["token"]
p = SECRETS / "dojo-token"
p.write_text(token)
p.chmod(0o600)
versions = {"gitlab": request("gitlab", "GET", "/api/v4/version"),
            "dojo_bridge_inventory": request("dojo", "GET", "/api/v2/auditor/repositories/")}
save("bootstrap-api.json", versions)
