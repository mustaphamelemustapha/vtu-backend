from fastapi.testclient import TestClient
from app.main import app
from app.dependencies import require_admin
import json

def override_require_admin():
    from app.models import User
    admin_user = User(id=1, email="admin@example.com", is_admin=True)
    return admin_user

app.dependency_overrides[require_admin] = override_require_admin

client = TestClient(app)
response = client.get("/api/v1/admin/transactions?tx_type=wallet_fund")
print("STATUS:", response.status_code)
print("BODY:", json.dumps(response.json(), indent=2))
