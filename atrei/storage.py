"""SQLite 持久化：提醒、待办、笔记、长期记忆。"""
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,   -- group / c2c / guild / dm / cli
    target_id TEXT NOT NULL,
    text TEXT NOT NULL,
    due_ts REAL NOT NULL,
    repeat TEXT NOT NULL DEFAULT 'none',
    status TEXT NOT NULL DEFAULT 'active',  -- active / undelivered / done / cancelled
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    text TEXT NOT NULL,
    due TEXT,
    priority TEXT NOT NULL DEFAULT 'normal',
    done INTEGER NOT NULL DEFAULT 0,
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    notebook TEXT NOT NULL,
    section TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    fact TEXT NOT NULL,
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""


class Storage:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def execute(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def all(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None

    # ---------- 提醒 ----------
    def add_reminder(self, user_id, target_kind, target_id, text, due_ts, repeat="none") -> int:
        cur = self.execute(
            "INSERT INTO reminders (user_id, target_kind, target_id, text, due_ts, repeat, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, target_kind, target_id, text, due_ts, repeat, time.time()),
        )
        return cur.lastrowid

    def list_reminders(self, user_id) -> list[dict]:
        return self.all(
            "SELECT * FROM reminders WHERE user_id=? AND status IN ('active','undelivered') ORDER BY due_ts",
            (user_id,),
        )

    def due_reminders(self, now_ts) -> list[dict]:
        return self.all("SELECT * FROM reminders WHERE status='active' AND due_ts<=?", (now_ts,))

    def undelivered_reminders(self, user_id) -> list[dict]:
        return self.all(
            "SELECT * FROM reminders WHERE user_id=? AND status='undelivered' ORDER BY due_ts", (user_id,)
        )

    def set_reminder(self, rid, **fields):
        keys = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE reminders SET {keys} WHERE id=?", (*fields.values(), rid))

    def cancel_reminder(self, user_id, rid) -> bool:
        cur = self.execute(
            "UPDATE reminders SET status='cancelled' WHERE id=? AND user_id=? AND status IN ('active','undelivered')",
            (rid, user_id),
        )
        return cur.rowcount > 0

    # ---------- 待办 ----------
    def add_todo(self, user_id, text, due=None, priority="normal") -> int:
        cur = self.execute(
            "INSERT INTO todos (user_id, text, due, priority, created_ts) VALUES (?, ?, ?, ?, ?)",
            (user_id, text, due, priority, time.time()),
        )
        return cur.lastrowid

    def list_todos(self, user_id, include_done=False) -> list[dict]:
        sql = "SELECT * FROM todos WHERE user_id=?" + ("" if include_done else " AND done=0")
        return self.all(sql + " ORDER BY done, CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, id", (user_id,))

    def complete_todo(self, user_id, tid) -> bool:
        return self.execute("UPDATE todos SET done=1 WHERE id=? AND user_id=?", (tid, user_id)).rowcount > 0

    def delete_todo(self, user_id, tid) -> bool:
        return self.execute("DELETE FROM todos WHERE id=? AND user_id=?", (tid, user_id)).rowcount > 0

    # ---------- 笔记 ----------
    def add_note(self, user_id, notebook, section, title, content) -> int:
        cur = self.execute(
            "INSERT INTO notes (user_id, notebook, section, title, content, created_ts) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, notebook, section, title, content, time.time()),
        )
        return cur.lastrowid

    def note_tree(self, user_id) -> list[dict]:
        return self.all(
            "SELECT notebook, section, COUNT(*) AS count FROM notes WHERE user_id=? "
            "GROUP BY notebook, section ORDER BY notebook, section",
            (user_id,),
        )

    def search_notes(self, user_id, keyword, limit=10) -> list[dict]:
        like = f"%{keyword}%"
        return self.all(
            "SELECT id, notebook, section, title, substr(content, 1, 120) AS preview FROM notes "
            "WHERE user_id=? AND (title LIKE ? OR content LIKE ? OR notebook LIKE ? OR section LIKE ?) "
            "ORDER BY id DESC LIMIT ?",
            (user_id, like, like, like, like, limit),
        )

    def notes_in(self, user_id, notebook, section=None) -> list[dict]:
        if section:
            return self.all(
                "SELECT id, title, substr(content, 1, 80) AS preview FROM notes WHERE user_id=? AND notebook=? AND section=? ORDER BY id",
                (user_id, notebook, section),
            )
        return self.all(
            "SELECT id, section, title, substr(content, 1, 80) AS preview FROM notes WHERE user_id=? AND notebook=? ORDER BY section, id",
            (user_id, notebook),
        )

    def get_note(self, user_id, nid) -> dict | None:
        return self.one("SELECT * FROM notes WHERE id=? AND user_id=?", (nid, user_id))

    def move_note(self, user_id, nid, notebook, section) -> bool:
        return self.execute(
            "UPDATE notes SET notebook=?, section=? WHERE id=? AND user_id=?", (notebook, section, nid, user_id)
        ).rowcount > 0

    def delete_note(self, user_id, nid) -> bool:
        return self.execute("DELETE FROM notes WHERE id=? AND user_id=?", (nid, user_id)).rowcount > 0

    def random_note(self, user_id, notebook=None) -> dict | None:
        if notebook:
            return self.one(
                "SELECT * FROM notes WHERE user_id=? AND notebook=? ORDER BY RANDOM() LIMIT 1", (user_id, notebook)
            )
        return self.one("SELECT * FROM notes WHERE user_id=? ORDER BY RANDOM() LIMIT 1", (user_id,))

    # ---------- 长期记忆 ----------
    def add_memory(self, user_id, fact) -> int:
        return self.execute(
            "INSERT INTO memories (user_id, fact, created_ts) VALUES (?, ?, ?)", (user_id, fact, time.time())
        ).lastrowid

    def list_memories(self, user_id) -> list[dict]:
        return self.all("SELECT id, fact FROM memories WHERE user_id=? ORDER BY id", (user_id,))

    def delete_memory(self, user_id, mid) -> bool:
        return self.execute("DELETE FROM memories WHERE id=? AND user_id=?", (mid, user_id)).rowcount > 0

    # ---------- 个人设置 ----------
    def get_setting(self, user_id, key, default=None):
        row = self.one("SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, key))
        return row["value"] if row else default

    def set_setting(self, user_id, key, value):
        self.execute(
            "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
            (user_id, key, value),
        )

    def stats(self, user_id) -> dict:
        def count(sql):
            return self.one(sql, (user_id,))["n"]

        return {
            "notes": count("SELECT COUNT(*) AS n FROM notes WHERE user_id=?"),
            "todos_open": count("SELECT COUNT(*) AS n FROM todos WHERE user_id=? AND done=0"),
            "todos_done": count("SELECT COUNT(*) AS n FROM todos WHERE user_id=? AND done=1"),
            "reminders": count("SELECT COUNT(*) AS n FROM reminders WHERE user_id=? AND status IN ('active','undelivered')"),
            "memories": count("SELECT COUNT(*) AS n FROM memories WHERE user_id=?"),
        }
