import json
import os
import platform
import sqlite3


MACOS_DB_PATH = os.path.expanduser("~/Library/Application Support/opencode/opencode.db")
LINUX_DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")


def discover_opencode_db(override: str | None = None) -> str:
    if override:
        return override
    env_path = os.environ.get("OPENCODE_DB")
    if env_path:
        return env_path
    system = platform.system()
    if system == "Darwin":
        if os.path.exists(MACOS_DB_PATH):
            return MACOS_DB_PATH
    elif system == "Linux":
        if os.path.exists(LINUX_DB_PATH):
            return LINUX_DB_PATH
    for path in [MACOS_DB_PATH, LINUX_DB_PATH]:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("Cannot find OpenCode database. Set OPENCODE_DB environment variable.")


def open_opencode_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _is_valid_worktree(path: str | None) -> bool:
    return bool(path) and os.path.abspath(path) != os.sep


def find_idle_sessions(opencode_conn: sqlite3.Connection, extracted_session_ids: set[str], idle_threshold_minutes: int, min_messages: int = 4) -> list[dict]:
    import time
    cutoff_ms = int((time.time() - idle_threshold_minutes * 60) * 1000)
    rows = opencode_conn.execute(
        "SELECT s.id, s.time_updated, p.worktree AS project_root "
        "FROM session s JOIN project p ON p.id = s.project_id "
        "WHERE s.time_updated < ? "
        "AND (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id) >= ?",
        (cutoff_ms, min_messages),
    ).fetchall()
    results = []
    for r in rows:
        if r["id"] in extracted_session_ids:
            continue
        if not _is_valid_worktree(r["project_root"]) or not os.path.isdir(r["project_root"]):
            continue
        results.append({"id": r["id"], "time_updated": r["time_updated"], "project_root": r["project_root"]})
    return results


def list_project_roots(opencode_conn: sqlite3.Connection) -> list[str]:
    rows = opencode_conn.execute("SELECT DISTINCT worktree FROM project WHERE worktree IS NOT NULL").fetchall()
    return [r["worktree"] for r in rows if _is_valid_worktree(r["worktree"]) and os.path.isdir(r["worktree"])]


def read_session_conversation(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT m.id as msg_id, m.data as msg_data, m.session_id, "
        "p.id as part_id, p.data as part_data, p.message_id "
        "FROM message m "
        "JOIN part p ON p.message_id = m.id "
        "WHERE m.session_id = ? "
        "ORDER BY m.time_created, p.time_created",
        (session_id,),
    ).fetchall()
    messages = {}
    for row in rows:
        msg_id = row["msg_id"]
        if msg_id not in messages:
            msg_data = json.loads(row["msg_data"])
            messages[msg_id] = {
                "id": msg_id,
                "session_id": row["session_id"],
                **msg_data,
                "parts": [],
            }
        part_data = json.loads(row["part_data"])
        part = {
            "id": row["part_id"],
            "session_id": row["session_id"],
            "messageID": row["message_id"],
            **part_data,
        }
        messages[msg_id]["parts"].append(part)
    return list(messages.values())
