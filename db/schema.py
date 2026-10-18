import os
import sqlite3
from pathlib import Path

_data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))
DB_PATH = _data_dir / "questions.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS questions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id   TEXT    UNIQUE NOT NULL,
            question    TEXT    NOT NULL,
            choices     TEXT    NOT NULL,   -- JSON {"A":"...","B":"..."}
            answer      TEXT    NOT NULL,   -- JSON ["A"] or ["A","C"]
            explanation TEXT,
            votes       TEXT,               -- JSON [{"option":"A","pct":70}, ...]
            domain      INTEGER NOT NULL CHECK(domain BETWEEN 1 AND 4),
            domain_name TEXT    NOT NULL,
            services    TEXT,               -- JSON ["EC2","S3"]
            embedding   BLOB,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_progress (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL REFERENCES questions(id),
            user_answer TEXT    NOT NULL,   -- JSON ["A"]
            is_correct  INTEGER NOT NULL,   -- 0/1
            answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_questions_domain ON questions(domain);
        CREATE INDEX IF NOT EXISTS idx_progress_qid    ON user_progress(question_id);
    """)
    conn.commit()
    conn.close()
