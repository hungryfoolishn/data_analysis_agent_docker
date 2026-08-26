"""Transactional SQLite metadata store with JSON snapshot compatibility."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class VersionConflictError(RuntimeError):
    pass


class SQLiteMetadataStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS run_snapshots (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_run_session_updated
                ON run_snapshots(session_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS run_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analysis_tasks (
                task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_steps (
                run_id TEXT NOT NULL, step_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, step_id)
            );
            CREATE TABLE IF NOT EXISTS executions (
                run_id TEXT NOT NULL, execution_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, execution_id)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                run_id TEXT NOT NULL, artifact_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, artifact_id)
            );
            CREATE TABLE IF NOT EXISTS findings (
                run_id TEXT NOT NULL, finding_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, finding_id)
            );
            CREATE TABLE IF NOT EXISTS plan_revisions (
                run_id TEXT NOT NULL, revision_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, revision_id)
            );
            """)

    def save_snapshot(self, snapshot: dict[str, Any], expected_version: int | None = None) -> int:
        run=snapshot["run"]; task=snapshot["task"]; run_id=run["run_id"]
        payload=json.dumps(snapshot,ensure_ascii=False,sort_keys=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT version FROM run_snapshots WHERE run_id=?",(run_id,)).fetchone()
            current=int(row[0]) if row else 0
            if expected_version is not None and current != expected_version:
                raise VersionConflictError(f"Run {run_id} version conflict: expected {expected_version}, found {current}")
            version=current+1
            db.execute("""INSERT INTO run_snapshots(run_id,task_id,session_id,status,version,updated_at,payload_json)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,version=excluded.version,
                updated_at=excluded.updated_at,payload_json=excluded.payload_json""",
                (run_id,run["task_id"],task["session_id"],run["status"],version,run["updated_at"],payload))
            db.execute("INSERT INTO run_events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (run_id,"snapshot",json.dumps({"version":version,"status":run["status"]}),run["updated_at"]))
            db.execute(
                "INSERT INTO analysis_tasks(task_id,session_id,payload_json) VALUES(?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET session_id=excluded.session_id,payload_json=excluded.payload_json",
                (task["task_id"], task["session_id"], json.dumps(task, ensure_ascii=False, sort_keys=True)),
            )
            entities = (
                ("run_steps", "step_id", run.get("steps", [])),
                ("executions", "execution_id", snapshot.get("executions", [])),
                ("artifacts", "artifact_id", snapshot.get("artifacts", [])),
                ("findings", "finding_id", snapshot.get("findings", [])),
                ("plan_revisions", "revision_id", run.get("plan_revisions", [])),
            )
            for table, identifier, items in entities:
                db.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
                for index, item in enumerate(items):
                    entity_id = str(item.get(identifier) or f"{identifier}_{index}")
                    db.execute(
                        f"INSERT INTO {table}(run_id,{identifier},payload_json) VALUES(?,?,?)",
                        (run_id, entity_id, json.dumps(item, ensure_ascii=False, sort_keys=True)),
                    )
            return version

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row=db.execute("SELECT payload_json FROM run_snapshots WHERE run_id=?",(run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_runs(self, session_id: str | None = None) -> list[dict[str, Any]]:
        sql="SELECT payload_json FROM run_snapshots"; params=()
        if session_id:
            sql+=" WHERE session_id=?"; params=(session_id,)
        sql+=" ORDER BY updated_at DESC"
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute(sql,params).fetchall()]

    def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows=db.execute("SELECT sequence,event_type,payload_json,created_at FROM run_events WHERE run_id=? AND sequence>? ORDER BY sequence",(run_id,after)).fetchall()
        return [{"sequence":r[0],"event_type":r[1],"payload":json.loads(r[2]),"created_at":r[3]} for r in rows]

    def migrate_workspace(self, workspace_root: Path) -> int:
        count=0
        for path in workspace_root.glob("*/.analysis_runs/run_*.json"):
            try:
                self.save_snapshot(json.loads(path.read_text(encoding="utf-8")))
                count+=1
            except (OSError,ValueError,KeyError,json.JSONDecodeError):
                continue
        return count
