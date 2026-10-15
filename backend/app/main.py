import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def build_database_url() -> URL:
    return URL.create(
        "mysql+pymysql",
        username=os.getenv("MYSQL_USER", "pezosys"),
        password=os.getenv("MYSQL_PASSWORD", "change-this-local-password"),
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DATABASE", "pezosyserp"),
    )


CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:8080",
    ).split(",")
    if origin.strip()
]

engine = create_engine(build_database_url(), pool_pre_ping=True)
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32)
SESSION_COOKIE = "pezosys_session"
SESSION_MAX_AGE = 60 * 60 * 8
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

app = FastAPI(title="Pezosys ERP API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount(
    "/assets",
    StaticFiles(directory=PROJECT_ROOT / "web" / "assets"),
    name="assets",
)
logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_id: str = Field(min_length=1, max_length=80, alias="loginId")
    full_name: str = Field(min_length=1, max_length=160, alias="fullName")
    email: str = Field(min_length=3, max_length=254)
    phone_no: str | None = Field(default=None, max_length=20, alias="phoneNo")
    role: Literal["admin", "employee"]
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_id: str = Field(min_length=1, max_length=80, alias="loginId")
    role: Literal["admin", "employee"]
    password: str = Field(min_length=1, max_length=128)


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=160, alias="fullName")
    email: str = Field(min_length=3, max_length=254)
    phone_no: str | None = Field(default=None, max_length=20, alias="phoneNo")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_status: Literal["approved", "rejected"] = Field(alias="approvalStatus")


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    return normalized


def _send_email_notification(
    recipient: str,
    subject: str,
    body: str,
) -> None:
    brevo_api_key = os.getenv("BREVO_API_KEY", "").strip()
    if brevo_api_key:
        sender = os.getenv("BREVO_FROM_EMAIL", "").strip()
        if not sender:
            raise ValueError("Set BREVO_FROM_EMAIL when BREVO_API_KEY is configured")
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": brevo_api_key,
                "content-type": "application/json",
            },
            json={
                "sender": {"email": sender},
                "to": [{"email": recipient}],
                "subject": subject,
                "textContent": body,
            },
            timeout=15,
        )
        response.raise_for_status()
        return

    resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
    if resend_api_key:
        sender = os.getenv("RESEND_FROM_EMAIL", "").strip()
        if not sender:
            raise ValueError("Set RESEND_FROM_EMAIL when RESEND_API_KEY is configured")
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": sender,
                "to": [recipient],
                "subject": subject,
                "text": body,
            },
            timeout=15,
        )
        response.raise_for_status()
        return

    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM_EMAIL", "").strip()
    username = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    if not host or not sender:
        raise ValueError("Set SMTP_HOST and SMTP_FROM_EMAIL in .env")
    if bool(username) != bool(password):
        raise ValueError("Set both SMTP_USER and SMTP_PASSWORD, or leave both blank")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    port = int(os.getenv("SMTP_PORT", "587"))
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls(context=context)
            smtp.ehlo()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def _try_send_email_notification(
    recipient: str,
    subject: str,
    body: str,
    notification_name: str,
) -> bool:
    try:
        if not recipient:
            raise ValueError("Notification recipient is not configured")
        _send_email_notification(recipient, subject, body)
    except (OSError, httpx.HTTPError, smtplib.SMTPException, ValueError) as error:
        logger.exception(
            "%s email notification failed (%s)",
            notification_name,
            type(error).__name__,
        )
        return False
    logger.info("%s email notification sent to %s", notification_name, recipient)
    return True


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return "scrypt${}${}${}${}${}".format(
        2**14,
        8,
        1,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived_key).decode("ascii"),
    )


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, n, r, p, encoded_salt, encoded_key = stored_hash.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected_key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
        actual_key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_key),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual_key, expected_key)


def _create_session_token(user_id: int) -> str:
    expires_at = int(time.time()) + SESSION_MAX_AGE
    payload = f"{user_id}.{expires_at}".encode("ascii")
    signature = hmac.new(
        SESSION_SECRET.encode("utf-8"), payload, hashlib.sha256
    ).digest()
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature_text = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{token}.{signature_text}"


def _session_user_id(request: Request) -> int:
    token = request.cookies.get(SESSION_COOKIE, "")
    try:
        encoded_payload, encoded_signature = token.split(".")
        payload = base64.urlsafe_b64decode(
            encoded_payload + "=" * (-len(encoded_payload) % 4)
        )
        signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        expected_signature = hmac.new(
            SESSION_SECRET.encode("utf-8"), payload, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("Invalid session signature")
        user_id_text, expires_text = payload.decode("ascii").split(".")
        if int(expires_text) < int(time.time()):
            raise ValueError("Session expired")
        return int(user_id_text)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=401, detail="Please sign in") from None


def _get_active_user(user_id: int) -> dict[str, object]:
    try:
        with engine.connect() as connection:
            user = (
                connection.execute(
                    text(
                        """
                        SELECT id, login_id, full_name, email, phoneno, role,
                               is_active, approval_status
                        FROM users
                        WHERE id = :user_id
                        """
                    ),
                    {"user_id": user_id},
                )
                .mappings()
                .first()
            )
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="Could not access the user database",
        ) from error

    if user is None or not user["is_active"] or user["approval_status"] != "approved":
        raise HTTPException(status_code=401, detail="Please sign in")
    return dict(user)


def _require_admin(request: Request) -> dict[str, object]:
    user = _get_active_user(_session_user_id(request))
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def _public_user(user: dict[str, object]) -> dict[str, object]:
    return {
        "id": user["id"],
        "loginId": user["login_id"],
        "fullName": user["full_name"],
        "email": user["email"],
        "phoneNo": user["phoneno"],
        "role": user["role"],
        "isActive": bool(user["is_active"]),
        "approvalStatus": user["approval_status"],
    }


def _set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        _create_session_token(user_id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


@app.get("/", include_in_schema=False)
def public_homepage() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "web" / "index.html")


@app.get("/signin.html", include_in_schema=False)
def signup_page() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "web" / "signin.html")


@app.get("/login.html", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "web" / "login.html")


@app.get("/admin-dashboard.html", include_in_schema=False)
def admin_dashboard_page() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "web" / "admin-dashboard.html")


@app.get("/auth.css", include_in_schema=False)
def auth_stylesheet() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "web" / "auth.css", media_type="text/css")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/database")
def database_health() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable",
        ) from error

    return {"status": "ok", "database": "connected"}


@app.post("/api/v1/auth/register", status_code=201)
def register_user(
    user: RegisterRequest,
    response: Response,
) -> dict[str, object]:
    login_id = user.login_id.strip()
    full_name = user.full_name.strip()
    email = _normalize_email(user.email)
    phone_no = user.phone_no.strip() if user.phone_no else None
    if not login_id or not full_name:
        raise HTTPException(status_code=422, detail="Name and login ID are required")

    approval_status = "pending" if user.role == "employee" else "approved"
    password_hash = _hash_password(user.password)
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    INSERT INTO users
                        (login_id, full_name, email, phoneno, role, password_hash,
                         approval_status)
                    VALUES
                        (:login_id, :full_name, :email, :phoneno, :role, :password_hash,
                         :approval_status)
                    """
                ),
                {
                    "login_id": login_id,
                    "full_name": full_name,
                    "email": email,
                    "phoneno": phone_no or None,
                    "role": user.role,
                    "password_hash": password_hash,
                    "approval_status": approval_status,
                },
            )
            user_id = int(result.lastrowid)
    except IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail="That login ID or email address is already registered",
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="Could not create account in the database",
        ) from error

    created_user = {
        "id": user_id,
        "login_id": login_id,
        "full_name": full_name,
        "email": email,
        "phoneno": phone_no or None,
        "role": user.role,
        "is_active": True,
        "approval_status": approval_status,
    }
    notification_sent = _try_send_email_notification(
        email,
        f"Welcome to Pezosys ERP, {full_name}",
        (
            f"Hello {full_name},\n\n"
            f"Your {user.role} account has been created with status "
            f"'{approval_status}'.\n"
            + (
                "An administrator must approve your account before you can log in.\n"
                if user.role == "employee"
                else "You can now log in to Pezosys ERP.\n"
            )
        ),
        "Signup confirmation",
    )
    admin_email = os.getenv("ADMIN_NOTIFICATION_EMAIL", "").strip()
    admin_notification_sent = _try_send_email_notification(
        admin_email,
        f"New {user.role} signup: {login_id}",
        (
            f"A new {user.role} account has registered.\n\n"
            f"Name: {full_name}\n"
            f"Login ID: {login_id}\n"
            f"Email: {email}\n"
            f"Approval status: {approval_status}\n"
        ),
        "Admin signup",
    )

    if user.role == "admin":
        _set_session_cookie(response, user_id)
        notification_message = (
            f"Admin account is approved. A confirmation email was sent to {email}."
            if notification_sent
            else f"Admin account is approved, but the confirmation email could not be sent to {email}."
        )
    else:
        notification_message = (
            f"Signup received. A confirmation email was sent to {email}. "
            "An administrator must approve your account before you can log in."
            if notification_sent
            else
            "Signup received. An administrator must approve your account before you can log in."
        )
        if not notification_sent:
            notification_message += (
                f" The signup confirmation email could not be sent to {email}; "
                "please contact the administrator."
            )

    return {
        "user": _public_user(created_user),
        "notificationSent": notification_sent,
        "adminNotificationSent": admin_notification_sent,
        "message": notification_message,
    }


@app.post("/api/v1/auth/login")
def login_user(
    credentials: LoginRequest,
    response: Response,
) -> dict[str, object]:
    try:
        with engine.connect() as connection:
            user = (
                connection.execute(
                    text(
                        """
                        SELECT id, login_id, full_name, email, phoneno, role,
                               password_hash, is_active, approval_status
                        FROM users
                        WHERE login_id = :login_id AND role = :role
                        """
                    ),
                    {
                        "login_id": credentials.login_id.strip(),
                        "role": credentials.role,
                    },
                )
                .mappings()
                .first()
            )
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="Could not access the user database",
        ) from error

    if user is None or not _verify_password(
        credentials.password, str(user["password_hash"])
    ):
        raise HTTPException(status_code=401, detail="Invalid login ID, role, or password")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="This account is inactive")
    if user["approval_status"] != "approved":
        raise HTTPException(
            status_code=403,
            detail=f"This account is {user['approval_status']}",
        )

    _set_session_cookie(response, int(user["id"]))
    login_email_sent = _try_send_email_notification(
        str(user["email"]),
        "New login to Pezosys ERP",
        (
            f"Hello {user['full_name']},\n\n"
            "Your Pezosys ERP account was just used to sign in.\n"
            f"Account type: {user['role']}\n"
            f"Login ID: {user['login_id']}\n\n"
            "If this wasn't you, contact your administrator.\n"
        ),
        "Login alert",
    )
    return {
        "user": _public_user(dict(user)),
        "notificationSent": login_email_sent,
        "message": (
            "Login successful. A sign-in alert was sent to your registered email."
            if login_email_sent
            else "Login successful, but the sign-in alert could not be sent. "
            "Check the SMTP configuration or contact your administrator."
        ),
    }


@app.get("/api/v1/auth/me")
def get_current_user(request: Request) -> dict[str, object]:
    user = _get_active_user(_session_user_id(request))
    return {"user": _public_user(user)}


@app.patch("/api/v1/auth/me")
def update_current_user(
    user_update: ProfileUpdateRequest,
    request: Request,
) -> dict[str, object]:
    user_id = _session_user_id(request)
    _get_active_user(user_id)
    full_name = user_update.full_name.strip()
    if not full_name:
        raise HTTPException(status_code=422, detail="Full name is required")
    email = _normalize_email(user_update.email)
    phone_no = user_update.phone_no.strip() if user_update.phone_no else None

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE users
                    SET full_name = :full_name, email = :email, phoneno = :phoneno
                    WHERE id = :user_id
                    """
                ),
                {
                    "full_name": full_name,
                    "email": email,
                    "phoneno": phone_no or None,
                    "user_id": user_id,
                },
            )
            user = (
                connection.execute(
                    text(
                        """
                        SELECT id, login_id, full_name, email, phoneno, role,
                               is_active, approval_status
                        FROM users
                        WHERE id = :user_id
                        """
                    ),
                    {"user_id": user_id},
                )
                .mappings()
                .first()
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail="That email address is already registered",
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="Could not update the profile in the database",
        ) from error

    if user is None:
        raise HTTPException(status_code=404, detail="User account was not found")
    return {"user": _public_user(dict(user))}


@app.get("/api/v1/admin/pending-employees")
def list_pending_employees(request: Request) -> dict[str, list[dict[str, object]]]:
    _require_admin(request)
    try:
        with engine.connect() as connection:
            users = (
                connection.execute(
                    text(
                        """
                        SELECT id, login_id, full_name, email, phoneno, role,
                               is_active, approval_status
                        FROM users
                        WHERE role = 'employee' AND approval_status = 'pending'
                        ORDER BY created_at ASC
                        """
                    )
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="Could not load pending employee accounts",
        ) from error
    return {"employees": [_public_user(dict(user)) for user in users]}


@app.patch("/api/v1/admin/employees/{employee_id}/approval")
def decide_employee_approval(
    employee_id: int,
    decision: ApprovalRequest,
    request: Request,
) -> dict[str, object]:
    _require_admin(request)
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE users
                    SET approval_status = :approval_status
                    WHERE id = :employee_id
                      AND role = 'employee'
                      AND approval_status = 'pending'
                    """
                ),
                {
                    "approval_status": decision.approval_status,
                    "employee_id": employee_id,
                },
            )
            if result.rowcount != 1:
                raise HTTPException(
                    status_code=404,
                    detail="Pending employee account was not found",
                )
            employee = (
                connection.execute(
                    text(
                        """
                        SELECT id, login_id, full_name, email, phoneno, role,
                               is_active, approval_status
                        FROM users
                        WHERE id = :employee_id
                        """
                    ),
                    {"employee_id": employee_id},
                )
                .mappings()
                .one()
            )
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="Could not update employee approval status",
        ) from error

    notification_sent = True
    message = f"Employee account {decision.approval_status}."
    try:
        _send_email_notification(
            str(employee["email"]),
            f"Your Pezosys ERP account was {decision.approval_status}",
            (
                f"Hello {employee['full_name']},\n\n"
                f"An administrator has {decision.approval_status} your employee "
                "account for Pezosys ERP.\n"
            ),
        )
    except (OSError, smtplib.SMTPException, ValueError) as error:
        notification_sent = False
        message += (
            " The employee email notification could not be sent. "
            f"Check the SMTP settings and notify them manually. Details: {error}"
        )
    return {
        "employee": _public_user(dict(employee)),
        "notificationSent": notification_sent,
        "message": message,
    }


@app.post("/api/v1/auth/logout", status_code=204)
def logout_user(response: Response) -> Response:
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax")
    response.status_code = 204
    return response
