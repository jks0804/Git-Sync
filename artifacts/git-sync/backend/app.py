import os
import sqlite3
import subprocess
import shutil
import tempfile
import threading
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "gitsyncd.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
scheduler = BackgroundScheduler(daemon=True)
sync_lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_branch TEXT NOT NULL DEFAULT 'main',
            dest_url TEXT NOT NULL,
            dest_branch TEXT NOT NULL DEFAULT 'main',
            schedule TEXT,
            created_at TEXT NOT NULL,
            last_sync TEXT,
            last_status TEXT
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            output TEXT,
            FOREIGN KEY (config_id) REFERENCES configs(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


def run_sync(config_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    if not row:
        conn.close()
        return
    config = dict(row)
    started_at = datetime.utcnow().isoformat()
    log_id = conn.execute(
        "INSERT INTO logs (config_id, started_at, status) VALUES (?, ?, 'running')",
        (config_id, started_at),
    ).lastrowid
    conn.commit()
    conn.close()

    output_lines = []
    status = "success"
    work_dir = tempfile.mkdtemp(prefix="gitsyncd_")

    try:
        source_dir = os.path.join(work_dir, "source")
        dest_dir = os.path.join(work_dir, "dest")

        output_lines.append(f"[{datetime.utcnow().isoformat()}] Cloning source: {config['source_url']} (branch: {config['source_branch']})")
        result = subprocess.run(
            ["git", "clone", "--branch", config["source_branch"], "--depth", "1", config["source_url"], source_dir],
            capture_output=True, text=True, timeout=120,
        )
        output_lines.append(result.stdout + result.stderr)
        if result.returncode != 0:
            raise RuntimeError(f"Clone source failed:\n{result.stderr}")

        output_lines.append(f"[{datetime.utcnow().isoformat()}] Cloning destination: {config['dest_url']} (branch: {config['dest_branch']})")
        dest_result = subprocess.run(
            ["git", "clone", "--branch", config["dest_branch"], config["dest_url"], dest_dir],
            capture_output=True, text=True, timeout=120,
        )
        if dest_result.returncode != 0:
            output_lines.append("Dest branch not found, cloning default and creating branch...")
            dest_result2 = subprocess.run(
                ["git", "clone", config["dest_url"], dest_dir],
                capture_output=True, text=True, timeout=120,
            )
            output_lines.append(dest_result2.stdout + dest_result2.stderr)
            if dest_result2.returncode != 0:
                raise RuntimeError(f"Clone dest failed:\n{dest_result2.stderr}")
        else:
            output_lines.append(dest_result.stdout + dest_result.stderr)

        output_lines.append(f"[{datetime.utcnow().isoformat()}] Syncing files from source to destination...")
        for item in os.listdir(dest_dir):
            if item == ".git":
                continue
            full_path = os.path.join(dest_dir, item)
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
            else:
                os.remove(full_path)

        for item in os.listdir(source_dir):
            if item == ".git":
                continue
            src_path = os.path.join(source_dir, item)
            dst_path = os.path.join(dest_dir, item)
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dst_path)
            else:
                shutil.copy2(src_path, dst_path)

        git_status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=dest_dir,
        )
        if not git_status.stdout.strip():
            output_lines.append(f"[{datetime.utcnow().isoformat()}] No changes detected. Nothing to push.")
        else:
            subprocess.run(["git", "config", "user.email", "gitsyncd@localhost"], cwd=dest_dir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "GitSyncd Bot"], cwd=dest_dir, capture_output=True)

            add_result = subprocess.run(
                ["git", "add", "-A"], capture_output=True, text=True, cwd=dest_dir,
            )
            output_lines.append(add_result.stdout + add_result.stderr)

            commit_result = subprocess.run(
                ["git", "commit", "-m", f"sync: from {config['source_url']} at {started_at}"],
                capture_output=True, text=True, cwd=dest_dir,
            )
            output_lines.append(commit_result.stdout + commit_result.stderr)
            if commit_result.returncode != 0:
                raise RuntimeError(f"Commit failed:\n{commit_result.stderr}")

            push_result = subprocess.run(
                ["git", "push", "origin", f"HEAD:{config['dest_branch']}"],
                capture_output=True, text=True, cwd=dest_dir, timeout=120,
            )
            output_lines.append(push_result.stdout + push_result.stderr)
            if push_result.returncode != 0:
                raise RuntimeError(f"Push failed:\n{push_result.stderr}")

            output_lines.append(f"[{datetime.utcnow().isoformat()}] Push complete.")

    except Exception as e:
        status = "error"
        output_lines.append(f"ERROR: {str(e)}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    finished_at = datetime.utcnow().isoformat()
    full_output = "\n".join(output_lines).strip()

    conn2 = get_db()
    conn2.execute(
        "UPDATE logs SET finished_at = ?, status = ?, output = ? WHERE id = ?",
        (finished_at, status, full_output, log_id),
    )
    conn2.execute(
        "UPDATE configs SET last_sync = ?, last_status = ? WHERE id = ?",
        (finished_at, status, config_id),
    )
    conn2.commit()
    conn2.close()


def schedule_config(config_id: int, cron_expr: str):
    job_id = f"sync_{config_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError("Cron expression must have 5 fields: minute hour day month day_of_week")
    trigger = CronTrigger(
        minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4]
    )
    scheduler.add_job(run_sync, trigger, args=[config_id], id=job_id, replace_existing=True)


def remove_schedule(config_id: int):
    job_id = f"sync_{config_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/v1/configs", methods=["GET"])
def list_configs():
    conn = get_db()
    rows = conn.execute("SELECT * FROM configs ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/v1/configs", methods=["POST"])
def create_config():
    data = request.get_json(force=True)
    required = ["name", "source_url", "dest_url"]
    for field in required:
        if not data.get(field, "").strip():
            return jsonify({"error": f"'{field}' is required"}), 400
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO configs (name, source_url, source_branch, dest_url, dest_branch, schedule, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            data["name"].strip(),
            data["source_url"].strip(),
            data.get("source_branch", "main").strip() or "main",
            data["dest_url"].strip(),
            data.get("dest_branch", "main").strip() or "main",
            data.get("schedule", "").strip() or None,
            datetime.utcnow().isoformat(),
        ),
    )
    config_id = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    conn.close()
    if row["schedule"]:
        try:
            schedule_config(config_id, row["schedule"])
        except Exception as e:
            return jsonify({"error": str(e)}), 400
    return jsonify(dict(row)), 201


@app.route("/v1/configs/<int:config_id>", methods=["PUT"])
def update_config(config_id):
    data = request.get_json(force=True)
    conn = get_db()
    row = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404

    name = data.get("name", row["name"]).strip()
    source_url = data.get("source_url", row["source_url"]).strip()
    source_branch = data.get("source_branch", row["source_branch"]).strip() or "main"
    dest_url = data.get("dest_url", row["dest_url"]).strip()
    dest_branch = data.get("dest_branch", row["dest_branch"]).strip() or "main"
    schedule = data.get("schedule", row["schedule"])
    if isinstance(schedule, str):
        schedule = schedule.strip() or None

    conn.execute(
        """UPDATE configs SET name=?, source_url=?, source_branch=?, dest_url=?, dest_branch=?, schedule=? WHERE id=?""",
        (name, source_url, source_branch, dest_url, dest_branch, schedule, config_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    conn.close()

    remove_schedule(config_id)
    if schedule:
        try:
            schedule_config(config_id, schedule)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    return jsonify(dict(updated))


@app.route("/v1/configs/<int:config_id>", methods=["DELETE"])
def delete_config(config_id):
    remove_schedule(config_id)
    conn = get_db()
    conn.execute("DELETE FROM logs WHERE config_id = ?", (config_id,))
    conn.execute("DELETE FROM configs WHERE id = ?", (config_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/v1/sync/<int:config_id>", methods=["POST"])
def trigger_sync(config_id):
    conn = get_db()
    row = conn.execute("SELECT id FROM configs WHERE id = ?", (config_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    thread = threading.Thread(target=run_sync, args=(config_id,), daemon=True)
    thread.start()
    return jsonify({"ok": True, "message": "Sync started"})


@app.route("/v1/logs", methods=["GET"])
def list_logs():
    config_id = request.args.get("config_id")
    conn = get_db()
    if config_id:
        rows = conn.execute(
            "SELECT l.*, c.name as config_name FROM logs l JOIN configs c ON l.config_id = c.id WHERE l.config_id = ? ORDER BY l.started_at DESC LIMIT 50",
            (config_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT l.*, c.name as config_name FROM logs l JOIN configs c ON l.config_id = c.id ORDER BY l.started_at DESC LIMIT 100",
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/v1/logs/<int:log_id>", methods=["GET"])
def get_log(log_id):
    conn = get_db()
    row = conn.execute(
        "SELECT l.*, c.name as config_name FROM logs l JOIN configs c ON l.config_id = c.id WHERE l.id = ?",
        (log_id,),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))


if __name__ == "__main__":
    init_db()
    scheduler.start()

    conn = get_db()
    rows = conn.execute("SELECT id, schedule FROM configs WHERE schedule IS NOT NULL").fetchall()
    conn.close()
    for row in rows:
        try:
            schedule_config(row["id"], row["schedule"])
        except Exception:
            pass

    port = int(os.environ.get("PORT", 20652))
    app.run(host="0.0.0.0", port=port, debug=False)
