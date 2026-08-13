"""
Self-service password change for both admin and customer accounts. Distinct
from "forgot password" (which this zero-cost app deliberately doesn't have,
since that needs a paid SMS/email service) -- this is a logged-in user
changing a password they still remember.
"""

from fastapi.testclient import TestClient

from app.auth import hash_password as admin_hash_password
from app.auth import verify_password as admin_verify_password
from app.database import SessionLocal
from app.models import Admin
from tests.helpers import make_customer


def _make_admin(db, email, password="originalpass1"):
    admin = Admin(name="Test Admin", email=email, password_hash=admin_hash_password(password), role="owner")
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def test_admin_can_change_own_password(fastapi_app):
    db = SessionLocal()
    admin = _make_admin(db, "changepass-admin@example.com")
    admin_id = admin.id
    db.close()

    client = TestClient(fastapi_app)
    login_response = client.post(
        "/admin/login", data={"email": "changepass-admin@example.com", "password": "originalpass1"}, follow_redirects=False
    )
    assert login_response.status_code == 303

    change_response = client.post(
        "/admin/change-password",
        data={"current_password": "originalpass1", "new_password": "brandnewpass1", "confirm_password": "brandnewpass1"},
    )
    assert change_response.status_code == 200
    assert "updated successfully" in change_response.text.lower()

    db = SessionLocal()
    admin = db.get(Admin, admin_id)
    assert admin_verify_password("brandnewpass1", admin.password_hash)
    assert not admin_verify_password("originalpass1", admin.password_hash)
    db.close()


def test_admin_change_password_rejects_wrong_current_password(fastapi_app):
    db = SessionLocal()
    _make_admin(db, "wrongcurrent-admin@example.com")
    db.close()

    client = TestClient(fastapi_app)
    client.post("/admin/login", data={"email": "wrongcurrent-admin@example.com", "password": "originalpass1"})

    response = client.post(
        "/admin/change-password",
        data={"current_password": "totallywrong", "new_password": "brandnewpass1", "confirm_password": "brandnewpass1"},
    )
    assert response.status_code == 400
    assert "incorrect" in response.text.lower()


def test_admin_change_password_requires_login():
    from app.main import app

    client = TestClient(app)
    # No login at all -- require_admin should redirect, never process the change.
    response = client.get("/admin/change-password", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_customer_can_change_own_password(fastapi_app):
    db = SessionLocal()
    customer = make_customer(db, mobile="9600000201", password="originalpass1")
    customer_id = customer.id
    db.close()

    client = TestClient(fastapi_app)
    login_response = client.post(
        "/login", data={"mobile": "9600000201", "password": "originalpass1", "next": ""}, follow_redirects=False
    )
    assert login_response.status_code == 303

    change_response = client.post(
        "/account/change-password",
        data={"current_password": "originalpass1", "new_password": "brandnewpass1", "confirm_password": "brandnewpass1"},
    )
    assert change_response.status_code == 200
    assert "updated successfully" in change_response.text.lower()

    db = SessionLocal()
    from app.models import Customer
    from app.auth import verify_password

    customer = db.get(Customer, customer_id)
    assert verify_password("brandnewpass1", customer.password_hash)
    db.close()

    # New password now works, old one doesn't.
    relogin_ok = client.post(
        "/login", data={"mobile": "9600000201", "password": "brandnewpass1", "next": ""}, follow_redirects=False
    )
    assert relogin_ok.status_code == 303
    relogin_old = TestClient(fastapi_app).post(
        "/login", data={"mobile": "9600000201", "password": "originalpass1", "next": ""}, follow_redirects=False
    )
    assert relogin_old.status_code == 401


def test_customer_change_password_rejects_short_new_password(fastapi_app):
    db = SessionLocal()
    make_customer(db, mobile="9600000202", password="originalpass1")
    db.close()

    client = TestClient(fastapi_app)
    client.post("/login", data={"mobile": "9600000202", "password": "originalpass1", "next": ""})

    response = client.post(
        "/account/change-password",
        data={"current_password": "originalpass1", "new_password": "short", "confirm_password": "short"},
    )
    assert response.status_code == 400
    assert "8 characters" in response.text
