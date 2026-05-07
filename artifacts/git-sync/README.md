# GitSyncd — Repo Sync Manager

A self-hosted web application for mirroring Git repositories between any two hosts. Designed to run as a single Python process with a plain HTML/CSS/JS frontend — no Node.js, no bundler, no framework overhead.

---

## What it does

GitSyncd lets you define *sync configurations* — each one maps a **source repo** to a **destination repo**, clones the source branch(es), copies all files into the destination, commits, and pushes. Syncs can be triggered manually, on a cron schedule, or via an inbound webhook from GitHub or GitLab.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask 3 |
| Database | SQLite (via stdlib `sqlite3`), stored at `backend/data/gitsyncd.db` |
| Scheduler | APScheduler 3 (`BackgroundScheduler` + `CronTrigger`) |
| Git operations | Shell-out to system `git` binary |
| Frontend | Vanilla HTML5 / CSS3 / JavaScript (no framework, no bundler) |
| Auth | Session-based (`flask.session`), passwords hashed with Werkzeug's `generate_password_hash` |

---

## Directory layout

```
artifacts/git-sync/
├── backend/
│   ├── app.py              # Entire backend — Flask app, DB schema, sync engine, API routes
│   ├── requirements.txt    # flask, apscheduler, gitpython
│   ├── data/
│   │   └── gitsyncd.db     # SQLite database (auto-created on first run)
│   └── static/
│       ├── index.html      # Main dashboard (authenticated)
│       ├── login.html      # Sign-in / register page (self-contained, inline JS)
│       ├── app.js          # All dashboard logic
│       └── style.css       # All styling
└── .replit-artifact/
    └── artifact.toml       # Replit routing config — serves on port 20652, path "/"
```

---

## Running locally (Replit)

The workflow command is:
```bash
cd /home/runner/workspace/artifacts/git-sync/backend && pip install -r requirements.txt -q && python app.py
```

**Required environment variable:**
- `SESSION_SECRET` — Flask session signing key. Set this in Replit Secrets. If absent, a random one is generated on each startup (invalidates all existing sessions on restart).

**Optional environment variables:**
- `ADMIN_USERNAME` — Default admin username (default: `admin`)
- `ADMIN_PASSWORD` — Default admin password (default: `admin123`)

---

## First run

1. The DB is created automatically with all tables on startup.
2. If no users exist, a default admin account is created from `ADMIN_USERNAME`/`ADMIN_PASSWORD` env vars.
3. The first user (lowest id) is always promoted to admin on every startup as a safety net.
4. Default credentials shown on the login page — change the password immediately via the user menu → Change Password.

---

## Database schema

### `users`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| username | TEXT UNIQUE | min 3 chars |
| password_hash | TEXT | Werkzeug pbkdf2 |
| created_at | TEXT | ISO 8601 UTC |
| is_admin | INTEGER | 0/1, migrated in |

### `configs`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT | Display name |
| source_url | TEXT | Clone-from URL |
| source_branch | TEXT | Legacy single-branch fallback |
| dest_url | TEXT | Push-to URL |
| dest_branch | TEXT | Legacy single-branch fallback |
| branches | TEXT | JSON array of `{"from":"…","to":"…"}` pairs |
| schedule | TEXT | 5-field cron expression or NULL |
| ssh_key | TEXT | Legacy shared SSH key (superseded by source/dest split) |
| git_username | TEXT | Legacy shared HTTPS username |
| git_password | TEXT | Legacy shared HTTPS password/token |
| source_ssh_key | TEXT | SSH private key for source repo |
| source_git_username | TEXT | HTTPS username for source repo |
| source_git_password | TEXT | HTTPS password/token for source repo |
| dest_ssh_key | TEXT | SSH private key for destination repo |
| dest_git_username | TEXT | HTTPS username for destination repo |
| dest_git_password | TEXT | HTTPS password/token for destination repo |
| webhook_secret | TEXT | HMAC secret for incoming webhooks, or NULL |
| created_at | TEXT | ISO 8601 UTC |
| last_sync | TEXT | ISO 8601 UTC of last sync attempt |
| last_status | TEXT | `success`, `error`, or NULL |

**Credential precedence at sync time:** `source_*` / `dest_*` columns are used first. If empty, falls back to the legacy shared `ssh_key`/`git_username`/`git_password`. The legacy columns are populated via a one-time backfill migration (gated by the `split_creds_backfill_v1` settings key) when upgrading from the old schema.

### `logs`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| config_id | INTEGER FK | Cascading delete |
| started_at | TEXT | ISO 8601 UTC |
| finished_at | TEXT | ISO 8601 UTC or NULL if running |
| status | TEXT | `running`, `success`, `error` |
| output | TEXT | Full git output — passwords/tokens are redacted to `***` before storage |
| trigger | TEXT | `manual`, `scheduled`, `webhook`, `github:push`, `gitlab:Push Hook`, etc. |

### `settings`
Key/value store. Known keys:

| Key | Value | Notes |
|---|---|---|
| `allow_registration` | `"1"` or `"0"` | Admin-controlled |
| `default_source_url` | URL string | Pre-fills source field in Add Sync modal |
| `default_dest_url` | URL string | Pre-fills destination field in Add Sync modal |
| `split_creds_backfill_v1` | ISO timestamp | One-time migration marker — prevents legacy creds re-propagating on every restart |

---

## How a sync works

1. `run_sync(config_id, trigger)` is called (in a daemon thread for manual/webhook triggers, or by APScheduler for scheduled ones).
2. A `logs` row is inserted with `status = 'running'`.
3. Two separate Git environments are built:
   - **Source env**: SSH key written to a tempfile with `chmod 600`, `GIT_SSH_COMMAND` set; HTTPS credentials percent-encoded into the source URL.
   - **Dest env**: Same, separately, for destination credentials.
4. For each branch pair in the config's `branches` list, `_sync_branch_pair()` is called:
   - `git clone --branch <src_branch> --depth 1 <src_url>` into a temp dir (using source env).
   - `git clone --branch <dst_branch> <dst_url>` into another temp dir (using dest env). If the destination branch doesn't exist, falls back to cloning the default branch.
   - All files (except `.git/`) are deleted from the destination clone.
   - All files (except `.git/`) from the source clone are copied into the destination clone.
   - `git status --porcelain` — if no changes, skips commit/push.
   - `git commit -m "sync(<trigger>): <branches> from <source_url> at <timestamp>"`
   - `git push origin HEAD:<dst_branch>` (using dest env).
5. All stdout/stderr from git commands is captured into `output_lines`.
6. Passwords and tokens are redacted from `output_lines` (both plain and percent-encoded forms) before being stored.
7. Temp dirs and SSH key files are cleaned up in `finally`.
8. The `logs` row is updated with final status and full output.

---

## API routes

All routes under `/v1/` require a valid session cookie unless noted.

### Auth

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/v1/auth/status` | Public | Returns `{allow_registration: bool}` |
| POST | `/v1/auth/login` | Public | `{username, password}` → sets session cookie |
| POST | `/v1/auth/register` | Public* | `{username, password}` — *only when `allow_registration=1` |
| POST | `/v1/auth/logout` | Session | Clears session |
| GET | `/v1/auth/me` | Session | Returns `{username, user_id, is_admin}` |
| PUT | `/v1/auth/password` | Session | `{current_password, new_password}` |

### Sync Configurations

| Method | Path | Description |
|---|---|---|
| GET | `/v1/configs` | List all configs (secrets stripped, boolean `has_*` flags returned) |
| POST | `/v1/configs` | Create config |
| GET | `/v1/configs/<id>` | Get single config |
| PUT | `/v1/configs/<id>` | Update config — `null` fields keep existing value, `""` clears |
| DELETE | `/v1/configs/<id>` | Delete config and all its logs |

### Sync & Logs

| Method | Path | Description |
|---|---|---|
| POST | `/v1/sync/<id>` | Trigger manual sync (async, returns immediately) |
| GET | `/v1/logs` | List logs; `?config_id=<id>` to filter; max 50 per config / 100 global |
| GET | `/v1/logs/<id>` | Get single log with full output |

### Webhooks

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/v1/configs/<id>/webhook` | Session | Get webhook URL + whether a secret is set |
| PUT | `/v1/configs/<id>/webhook` | Session | `{action: "save"\|"generate"\|"clear", secret?: "…"}` |
| POST | `/webhooks/<id>` | Public | Receives GitHub/GitLab/generic webhook, triggers sync |

Webhook signature verification order:
- **GitHub**: `X-Hub-Signature-256: sha256=<HMAC-SHA256>`
- **GitLab**: `X-Gitlab-Token: <secret>`
- **Generic**: `X-Webhook-Secret: <secret>`
- If no secret is configured, all incoming webhooks are accepted (open webhook).
- `hmac.compare_digest` is used throughout (constant-time).

The `trigger` field in logs will be set to `github:push`, `gitlab:Push Hook`, `webhook`, etc. based on the incoming headers.

### Settings & Users

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/v1/settings` | Session | All settings key/value pairs |
| PUT | `/v1/settings` | Session | Update `default_source_url`, `default_dest_url`; admins also `allow_registration` |
| GET | `/v1/users` | Admin | List all users |
| DELETE | `/v1/users/<id>` | Admin | Delete user (cannot delete self or last admin) |
| PUT | `/v1/users/<id>/role` | Admin | `{is_admin: bool}` (cannot demote self or last admin) |

---

## Authentication modes per sync config

Each sync config carries **independent** credentials for source and destination. The form has two separate sections: **Source Authentication** and **Destination Authentication**.

### SSH
- Paste the full PEM private key text into the SSH Key textarea for the relevant side.
- Written to a tempfile with `chmod 600`, used for the git operation, then deleted immediately.
- Never returned from the API — only `has_source_ssh_key` / `has_dest_ssh_key` boolean flags are exposed.

### HTTPS
- Username + password or personal access token.
- Credentials are percent-encoded (special chars handled) and embedded in the clone/push URL.
- The password is never returned from the API — only `has_source_git_password` / `has_dest_git_password` booleans.
- When editing a config, leaving the password field blank preserves the existing saved value.
- All sync log output is scrubbed of raw and percent-encoded credential strings before storage.

---

## Scheduling

Cron expressions follow the standard 5-field format (`minute hour day month day_of_week`). APScheduler parses these and runs `run_sync(config_id, "scheduled")` in a background thread. Schedules are re-registered from the database on every app startup.

Preset examples available in the UI:
- `*/15 * * * *` — every 15 minutes
- `0 * * * *` — hourly
- `0 0 * * *` — daily at midnight UTC
- `0 0 * * 0` — weekly on Sunday at midnight UTC

---

## IAM / User management

- The first user ever created (lowest `id`) is always an admin.
- Admins can view all users, toggle admin role for any user (except removing their own or the last admin), and delete users (except themselves or the last admin).
- Admins can toggle open registration on/off from the Settings panel.
- All users can change their own password.
- Session cookies are `HttpOnly` + `SameSite=Lax`.

---

## Frontend

The frontend is entirely vanilla — no framework, no build step. Three files:

- **`login.html`** — self-contained with inline `<script>`. Shows sign-in form; conditionally shows a Create Account tab when `allow_registration=1`. Checks `/v1/auth/status` on load.
- **`index.html`** — dashboard shell, modal markup (Add/Edit config, Settings, Logs, Webhook, Change Password). All interactivity in `app.js`.
- **`app.js`** — all dashboard logic: card rendering, modal open/close, form submission, auth tab switching (per source/destination side), cron presets, settings, user management, webhook management.
- **`style.css`** — dark theme, card grid, modals, toast notifications, branch pills, auth indicators.

Key UI patterns:
- `apiFetch(path, options)` — wraps `fetch`, attaches `credentials: "same-origin"`, auto-redirects to `/login` on 401.
- `renderCard(config)` — produces the config card HTML from a config object. Auth badges show per-direction SSH/token indicators.
- `switchAuthTab(side, tab)` — `side` is `"src"` or `"dst"`, `tab` is `"ssh"` or `"https"`. Each direction has its own independent tab state.
- `submitConfigForm` — sends `source_ssh_key`, `source_git_username`, `source_git_password`, `dest_ssh_key`, `dest_git_username`, `dest_git_password` to the API.

---

## Planned / in-progress features

These are built and working:
- Multi-branch sync (arbitrary `{from, to}` branch pair lists per config)
- Per-direction credentials (separate source and destination SSH key or HTTPS token)
- Webhook management UI (generate/save/clear secret, GitHub + GitLab + generic signature verification)
- Default Git instance URL settings (pre-fills URL fields on new config)
- Admin user management (list, promote, demote, delete users)
- Open/closed registration toggle

### Future ideas (not yet started)
- Dry-run / diff preview before sync
- Real-time log streaming (SSE or WebSocket)
- Sync status badges / embeddable shields
- Notification integrations (Slack, email on failure)
- Sync groups / dependency ordering (run B after A succeeds)
- OAuth login in addition to local accounts
- Configurable log retention / auto-pruning

---

## Security notes

- Passwords hashed with Werkzeug PBKDF2-SHA256.
- SSH private keys stored in SQLite but never returned in API responses.
- HTTPS tokens stored in SQLite, redacted from sync logs, never returned in API responses.
- Webhook HMAC uses `hmac.compare_digest` (constant-time).
- `split_creds_backfill_v1` settings key gates the one-time credential migration — safe across restarts.
- This runs Flask's built-in development server. For production, put it behind gunicorn/uvicorn and a TLS-terminating reverse proxy (e.g. nginx, Caddy).
