import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from backend.app import main
from backend.app.main import app, build_database_url


client = TestClient(app)


@pytest.fixture
def database(monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with test_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login_id VARCHAR(80) NOT NULL,
                full_name VARCHAR(160) NOT NULL,
                email VARCHAR(254) NOT NULL UNIQUE,
                phoneno VARCHAR(20),
                role VARCHAR(8) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                approval_status VARCHAR(10) NOT NULL DEFAULT 'approved',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (login_id, role)
            )
            """
        )
    monkeypatch.setattr(main, "engine", test_engine)
    sent_emails = []
    monkeypatch.setattr(
        main,
        "_send_email_notification",
        lambda recipient, subject, body: sent_emails.append(
            {"recipient": recipient, "subject": subject, "body": body}
        ),
    )
    monkeypatch.setenv("ADMIN_NOTIFICATION_EMAIL", "admin@example.com")
    yield {"engine": test_engine, "emails": sent_emails}
    test_engine.dispose()


@pytest.fixture
def unauthenticated_client():
    with TestClient(app) as test_client:
        yield test_client


def test_liveness_endpoint_reports_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_public_homepage_is_served() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Pezosys ERP" in response.text


def test_flutter_web_origin_is_allowed() -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:8080"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8080"


def test_database_url_uses_mysql_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("MYSQL_HOST", "db.example.test")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_USER", "erp_user")
    monkeypatch.setenv("MYSQL_PASSWORD", "special$pass#word")
    monkeypatch.setenv("MYSQL_DATABASE", "erp_test")

    url = build_database_url()

    assert url.drivername == "mysql+pymysql"
    assert url.host == "db.example.test"
    assert url.port == 3307
    assert url.username == "erp_user"
    assert url.password == "special$pass#word"
    assert url.database == "erp_test"


def test_smtp_notification_uses_configured_tls_and_credentials(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["server"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def ehlo(self):
            sent["ehlo"] = True

        def starttls(self, context):
            sent["tls_context"] = context

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(main.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(main.ssl, "create_default_context", lambda: "tls-context")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "smtp-user")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-password")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@example.test")
    monkeypatch.setenv("SMTP_USE_TLS", "true")

    main._send_email_notification(
        "admin@example.test",
        "New employee signup",
        "An employee registered.",
    )

    assert sent["server"] == ("smtp.example.test", 587, 15)
    assert sent["tls_context"] == "tls-context"
    assert sent["login"] == ("smtp-user", "smtp-password")
    assert sent["message"]["To"] == "admin@example.test"
    assert sent["message"]["Subject"] == "New employee signup"


def test_register_login_and_edit_employee_profile(database, unauthenticated_client):
    registration = unauthenticated_client.post(
        "/api/v1/auth/register",
        json={
            "loginId": "employee-1",
            "fullName": "Example Employee",
            "email": "EMPLOYEE@example.com",
            "phoneNo": "555-0100",
            "role": "employee",
            "password": "strong-password",
        },
    )

    assert registration.status_code == 201
    assert registration.json()["user"]["email"] == "employee@example.com"
    assert registration.json()["user"]["approvalStatus"] == "pending"
    assert registration.json()["notificationSent"] is True
    assert registration.json()["adminNotificationSent"] is True
    assert database["emails"][0]["recipient"] == "employee@example.com"
    assert database["emails"][0]["subject"] == "Welcome to Pezosys ERP, Example Employee"
    assert database["emails"][1]["recipient"] == "admin@example.com"
    assert "password_hash" not in registration.json()["user"]
    assert "set-cookie" not in registration.headers

    login = unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "loginId": "employee-1",
            "role": "employee",
            "password": "strong-password",
        },
    )
    assert login.status_code == 403
    assert login.json()["detail"] == "This account is pending"

    admin_registration = unauthenticated_client.post(
        "/api/v1/auth/register",
        json={
            "loginId": "approving-admin",
            "fullName": "Approving Admin",
            "email": "approver@example.com",
            "role": "admin",
            "password": "strong-password",
        },
    )
    assert admin_registration.status_code == 201
    assert admin_registration.json()["user"]["approvalStatus"] == "approved"
    assert "HttpOnly" in admin_registration.headers["set-cookie"]
    pending = unauthenticated_client.get("/api/v1/admin/pending-employees")
    assert pending.status_code == 200
    assert [user["loginId"] for user in pending.json()["employees"]] == ["employee-1"]

    employee_id = pending.json()["employees"][0]["id"]
    approval = unauthenticated_client.patch(
        f"/api/v1/admin/employees/{employee_id}/approval",
        json={"approvalStatus": "approved"},
    )
    assert approval.status_code == 200
    assert approval.json()["employee"]["approvalStatus"] == "approved"
    assert approval.json()["notificationSent"] is True
    assert database["emails"][-1]["recipient"] == "employee@example.com"

    login = unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "loginId": "employee-1",
            "role": "employee",
            "password": "strong-password",
        },
    )
    assert login.status_code == 200
    assert login.json()["notificationSent"] is True
    assert login.json()["message"] == (
        "Login successful. A sign-in alert was sent to your registered email."
    )
    assert database["emails"][-1]["recipient"] == "employee@example.com"
    assert database["emails"][-1]["subject"] == "New login to Pezosys ERP"

    profile = unauthenticated_client.patch(
        "/api/v1/auth/me",
        json={
            "fullName": "Updated Employee",
            "email": "updated@example.com",
            "phoneNo": None,
        },
    )
    assert profile.status_code == 200
    assert profile.json()["user"]["fullName"] == "Updated Employee"
    assert profile.json()["user"]["phoneNo"] is None


def test_register_admin_and_login_requires_the_selected_role(
    database, unauthenticated_client
):
    registration = unauthenticated_client.post(
        "/api/v1/auth/register",
        json={
            "loginId": "shared-login",
            "fullName": "Example Admin",
            "email": "admin@example.com",
            "role": "admin",
            "password": "strong-password",
        },
    )
    assert registration.status_code == 201
    assert registration.json()["user"]["role"] == "admin"
    assert registration.json()["user"]["approvalStatus"] == "approved"

    wrong_role_login = unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "loginId": "shared-login",
            "role": "employee",
            "password": "strong-password",
        },
    )
    assert wrong_role_login.status_code == 401

    admin_login = unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "loginId": "shared-login",
            "role": "admin",
            "password": "strong-password",
        },
    )
    assert admin_login.status_code == 200
    admin_profile = unauthenticated_client.patch(
        "/api/v1/auth/me",
        json={
            "fullName": "Updated Admin",
            "email": "updated-admin@example.com",
            "phoneNo": None,
        },
    )
    assert admin_profile.status_code == 200
    assert admin_profile.json()["user"]["role"] == "admin"
    assert admin_profile.json()["user"]["fullName"] == "Updated Admin"


def test_signup_and_login_pages_are_served(unauthenticated_client):
    home = unauthenticated_client.get("/")
    signup = unauthenticated_client.get("/signin.html")
    login = unauthenticated_client.get("/login.html")

    assert home.status_code == 200
    assert "/assets/pezosys-logo.png" in home.text
    assert 'rel="icon" type="image/png" href="/assets/pezosys-logo.png"' in home.text
    assert "Work flows" in home.text
    assert 'href="/login.html"' in home.text
    assert signup.status_code == 200
    assert 'value="admin"' in signup.text
    assert 'value="employee"' in signup.text
    assert login.status_code == 200
    assert "Your profile" in login.text
    assert "Employee approval requests" in login.text
    assert "Account type" in login.text
    assert "login-brand-panel" not in login.text
    assert "login-panel" in login.text
    assert "/assets/pezosys-logo.png" in signup.text
    assert 'rel="icon" type="image/png" href="/assets/pezosys-logo.png"' in signup.text
    assert "/assets/pezosys-logo.png" in login.text
    assert 'rel="icon" type="image/png" href="/assets/pezosys-logo.png"' in login.text
    assert "login-brand-panel" in home.text
    stylesheet = unauthenticated_client.get("/auth.css")
    assert stylesheet.status_code == 200
    assert 'url("/assets/login-background.png")' in stylesheet.text
    assert 'url("/assets/home-background.jpg")' in stylesheet.text


def test_brand_and_background_assets_are_served(unauthenticated_client):
    asset_paths = (
        "/assets/pezosys-logo.png",
        "/assets/home-background.jpg",
        "/assets/signup-background.jpg",
        "/assets/login-background.png",
    )

    for asset_path in asset_paths:
        response = unauthenticated_client.get(asset_path)
        assert response.status_code == 200
        assert response.content


def test_duplicate_user_and_invalid_password_are_rejected(
    database, unauthenticated_client
):
    user_data = {
        "loginId": "employee-2",
        "fullName": "Example Employee",
        "email": "employee2@example.com",
        "role": "employee",
        "password": "strong-password",
    }
    assert unauthenticated_client.post(
        "/api/v1/auth/register", json=user_data
    ).status_code == 201
    assert unauthenticated_client.post(
        "/api/v1/auth/register", json=user_data
    ).status_code == 409

    failed_login = unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "loginId": "employee-2",
            "role": "employee",
            "password": "wrong-password",
        },
    )
    assert failed_login.status_code == 401


def test_only_admin_can_list_or_approve_employees(database, unauthenticated_client):
    pending_registration = unauthenticated_client.post(
        "/api/v1/auth/register",
        json={
            "loginId": "pending-employee",
            "fullName": "Pending Employee",
            "email": "pending@example.com",
            "role": "employee",
            "password": "strong-password",
        },
    )
    employee_id = pending_registration.json()["user"]["id"]

    assert unauthenticated_client.get(
        "/api/v1/admin/pending-employees"
    ).status_code == 401
    assert unauthenticated_client.patch(
        f"/api/v1/admin/employees/{employee_id}/approval",
        json={"approvalStatus": "approved"},
    ).status_code == 401

    with database["engine"].begin() as connection:
        connection.execute(
            text(
                "UPDATE users SET approval_status = 'approved' WHERE id = :user_id"
            ),
            {"user_id": employee_id},
        )
    employee_login = unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "loginId": "pending-employee",
            "role": "employee",
            "password": "strong-password",
        },
    )
    assert employee_login.status_code == 200
    assert unauthenticated_client.get(
        "/api/v1/admin/pending-employees"
    ).status_code == 403
    assert unauthenticated_client.patch(
        f"/api/v1/admin/employees/{employee_id}/approval",
        json={"approvalStatus": "rejected"},
    ).status_code == 403

    with database["engine"].begin() as connection:
        connection.execute(
            text(
                "UPDATE users SET approval_status = 'pending' WHERE id = :user_id"
            ),
            {"user_id": employee_id},
        )

    admin_registration = unauthenticated_client.post(
        "/api/v1/auth/register",
        json={
            "loginId": "admin-for-approval",
            "fullName": "Admin",
            "email": "admin-for-approval@example.com",
            "role": "admin",
            "password": "strong-password",
        },
    )
    assert admin_registration.status_code == 201
    rejected = unauthenticated_client.patch(
        f"/api/v1/admin/employees/{employee_id}/approval",
        json={"approvalStatus": "rejected"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["employee"]["approvalStatus"] == "rejected"
    rejected_login = unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "loginId": "pending-employee",
            "role": "employee",
            "password": "strong-password",
        },
    )
    assert rejected_login.status_code == 403
    assert rejected_login.json()["detail"] == "This account is rejected"
    assert database["emails"][-1]["recipient"] == "pending@example.com"


def test_signup_reports_missing_email_delivery(database, unauthenticated_client, monkeypatch):
    def fail_notification(recipient, subject, body):
        raise main.smtplib.SMTPException("SMTP unavailable")

    monkeypatch.setattr(main, "_send_email_notification", fail_notification)
    response = unauthenticated_client.post(
        "/api/v1/auth/register",
        json={
            "loginId": "email-failure",
            "fullName": "Email Failure",
            "email": "email-failure@example.com",
            "role": "employee",
            "password": "strong-password",
        },
    )

    assert response.status_code == 201
    assert response.json()["user"]["approvalStatus"] == "pending"
    assert response.json()["notificationSent"] is False
    assert "confirmation email could not be sent" in response.json()["message"]


def test_login_succeeds_if_login_alert_email_fails(database, unauthenticated_client, monkeypatch):
    registration = unauthenticated_client.post(
        "/api/v1/auth/register",
        json={
            "loginId": "login-email-failure",
            "fullName": "Login Email Failure",
            "email": "login-failure@example.com",
            "role": "admin",
            "password": "strong-password",
        },
    )
    assert registration.status_code == 201

    def fail_notification(recipient, subject, body):
        raise main.smtplib.SMTPException("SMTP unavailable")

    monkeypatch.setattr(main, "_send_email_notification", fail_notification)
    login = unauthenticated_client.post(
        "/api/v1/auth/login",
        json={
            "loginId": "login-email-failure",
            "role": "admin",
            "password": "strong-password",
        },
    )

    assert login.status_code == 200
    assert login.json()["notificationSent"] is False
    assert "sign-in alert could not be sent" in login.json()["message"]
