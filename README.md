# Pezosys ERP

Starter foundation for the Pezosys ERP public website and Flutter mobile/web app.

## Stack

- `web/`: public HTML entry page, served by the Python API.
- `web/assets/`: Pezosys logo and page background images.
- `flutter_app/`: shared Flutter app for Android and web.
- `backend/`: FastAPI service and MySQL health check.
- `compose.yaml`: local MySQL 8.4 service.

## Connect to your existing MySQL database

The API connects directly to MySQL; Docker and MySQL Workbench are not needed
to run it. Workbench is a graphical client for managing the same MySQL server.
Set these values in `.env` to match your MySQL server and database:

```env
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=your_mysql_user
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=pezosyserp
```

The existing `users` table must have the columns in your supplied schema. The
API does not create or modify that table. Use a dedicated MySQL application user
with only the needed permissions instead of using the MySQL `root` account.

## Start the backend

1. Create a Python virtual environment and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements-dev.txt
   ```

2. Start the API from the repository root:

   ```powershell
   uvicorn backend.app.main:app --reload --env-file .env
   ```

The public page is at `http://localhost:8000/`, the API liveness check is at
`http://localhost:8000/health`, and the database check is at
`http://localhost:8000/api/v1/health/database`. Interactive API docs are at
`http://localhost:8000/docs`.

Account creation is at `http://localhost:8000/signin.html`; login and profile
editing are at `http://localhost:8000/login.html`. Both admin and employee
profiles can edit their full name, email, and phone number. Passwords are
stored as scrypt hashes, and login is selected by both login ID and role.

Employee registrations are created with `approval_status='pending'` and cannot
log in until an admin approves them from the login/profile page. Admin accounts
remain immediately active. The admin approval actions are authorized by the
server, not just hidden in the page.

Signup notifications go to `ADMIN_NOTIFICATION_EMAIL`. Approval/rejection
notifications go to the employee. Signup confirmations and successful-login
alerts go to the email address entered for that user's account. Admins are
created with `approved` status; employees are created with `pending` status
until an admin approves them. Configure SMTP in `.env`:

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-smtp-login
SMTP_PASSWORD=your-smtp-password
SMTP_FROM_EMAIL=notifications@example.com
SMTP_USE_TLS=true
ADMIN_NOTIFICATION_EMAIL=admin@example.com
```

If your SMTP provider allows unauthenticated relaying, leave `SMTP_USER` and
`SMTP_PASSWORD` blank. Use the TLS and port required by your mail provider.
If email delivery fails, the account/approval status is still saved and the
page reports that the notification was not sent.

Mailjet is also supported for Render deployments through its HTTPS API. Verify
the sender email in Mailjet, then configure `MAILJET_API_KEY`,
`MAILJET_API_SECRET`, and `MAILJET_FROM_EMAIL`. When configured, Mailjet takes
priority over the SMTP fallback.

Admin registration is currently public, as requested. Anyone who can reach
the signup page can create an administrator account. Restrict admin signup
before deploying this site or exposing it to the internet.

For local development, the API generates a temporary session-signing secret
when `SESSION_SECRET` is not set; restarting the API invalidates existing
sessions. For deployment, set a stable, random `SESSION_SECRET` and
`COOKIE_SECURE=true` when serving over HTTPS.

If you choose to run a separate MySQL container with Docker Compose instead,
set `MYSQL_HOST=mysql` and configure the Compose MySQL environment in
`.env.example` before starting it. Do not run that container when using your
existing local MySQL server.

## Run the Flutter app

Install Flutter, then generate the Android and web platform folders from
`flutter_app/`:

```powershell
cd flutter_app
flutter create --platforms=android,web .
flutter pub get
flutter run -d chrome --web-port 8080 --dart-define=API_BASE_URL=http://localhost:8000
```

For a physical Android phone, connect the phone and development computer to the
same network, allow port `8000` through the computer firewall, and use the
computer's LAN IP instead of `localhost`:

```powershell
flutter run -d <device-id> --dart-define=API_BASE_URL=http://<computer-lan-ip>:8000
```

For local Android testing over HTTP, add `android:usesCleartextTraffic="true"`
to the `<application>` element in `android/app/src/main/AndroidManifest.xml`.
Keep this development-only setting disabled for production; use HTTPS for
Play Store builds.

Use HTTPS and production credentials before exposing the API publicly or
publishing an app to the Play Store. The Flutter project is not yet configured
for production signing or a release build.

## Tests

```powershell
pytest
```
