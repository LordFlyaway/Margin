"""SQLite storage. One connection per thread, autocommit, WAL."""
from __future__ import annotations

import sqlite3
import threading
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  title TEXT NOT NULL, filename TEXT NOT NULL, path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'processing', detail TEXT,
  page_count INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS pages(
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(document_id, page));

CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page INTEGER NOT NULL, text TEXT NOT NULL, embedding BLOB);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text); END;

CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  title TEXT NOT NULL, style TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'writing', created_at TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS cards(
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  front TEXT NOT NULL, back TEXT NOT NULL, page INTEGER,
  ease REAL NOT NULL DEFAULT 2.5, interval_days REAL NOT NULL DEFAULT 0,
  reps INTEGER NOT NULL DEFAULT 0, lapses INTEGER NOT NULL DEFAULT 0,
  due TEXT NOT NULL DEFAULT (date('now','localtime')));

CREATE TABLE IF NOT EXISTS quizzes(
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'writing', score REAL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS questions(
  id INTEGER PRIMARY KEY,
  quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, prompt TEXT NOT NULL, options TEXT,
  answer_index INTEGER, model_answer TEXT NOT NULL, explanation TEXT NOT NULL,
  topic TEXT NOT NULL, page INTEGER, document_id INTEGER,
  user_answer TEXT, score REAL, feedback TEXT);

CREATE TABLE IF NOT EXISTS exams(
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  name TEXT NOT NULL, date TEXT NOT NULL, minutes_per_day INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'planning', detail TEXT, topics TEXT NOT NULL DEFAULT '[]');

CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY,
  exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  date TEXT NOT NULL, topic TEXT NOT NULL, kind TEXT NOT NULL,
  minutes INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(date);

CREATE TABLE IF NOT EXISTS music(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, spotify_id TEXT NOT NULL, title TEXT NOT NULL,
  thumbnail TEXT, UNIQUE(kind, spotify_id));

CREATE TABLE IF NOT EXISTS papers(
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'writing', detail TEXT,
  body TEXT NOT NULL DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL,
  sources TEXT NOT NULL DEFAULT '[]', via TEXT NOT NULL DEFAULT 'text',
  attachments TEXT NOT NULL DEFAULT '[]',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

_local = threading.local()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        config.ensure_dirs()
        c = sqlite3.connect(config.DB_PATH, isolation_level=None, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        _local.conn = c
    return c


def init() -> None:
    conn().executescript(SCHEMA)
    if "done_at" not in {r["name"] for r in q("PRAGMA table_info(tasks)")}:
        run("ALTER TABLE tasks ADD COLUMN done_at TEXT")
    if "kind" not in {r["name"] for r in q("PRAGMA table_info(documents)")}:
        run("ALTER TABLE documents ADD COLUMN kind TEXT NOT NULL DEFAULT 'notes'")  # 'notes' | 'paper'
    if "attachments" not in {r["name"] for r in q("PRAGMA table_info(messages)")}:
        run("ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
    # Anything still "in progress" was cut off by the app closing.
    # Documents are excluded: ingest.resume_unfinished picks those back up.
    run("UPDATE notes SET status='failed' WHERE status='writing'")
    run("UPDATE papers SET status='failed', detail='Margin closed before this finished.' WHERE status='writing'")
    run("UPDATE quizzes SET status='failed' WHERE status='writing'")
    run("UPDATE exams SET status='failed', detail='Margin closed before this finished.' WHERE status='planning'")


def run(sql: str, *args: Any) -> int:
    return conn().execute(sql, args).lastrowid


def many(sql: str, rows: list[tuple]) -> None:
    c = conn()
    c.execute("BEGIN")
    try:
        c.executemany(sql, rows)
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def q(sql: str, *args: Any) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, *args: Any) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None
