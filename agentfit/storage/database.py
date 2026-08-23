import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Iterable
from agentfit.models.events import UsageEvent


class DatabaseManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _get_conn(self, readonly: bool = False):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if readonly:
            conn.execute("PRAGMA query_only = ON;")
        return conn

    def _rows_to_events(self, rows: Iterable[sqlite3.Row]) -> list[UsageEvent]:
        events: list[UsageEvent] = []
        for row in rows:
            events.append(UsageEvent(
                event_id=row["event_id"],
                provider=row["provider"],
                session_id=row["session_id"],
                project_hash=row["project_hash"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                model=row["model"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                cache_read_tokens=row["cache_read_tokens"],
                cache_write_tokens=row["cache_write_tokens"],
                reasoning_tokens=row["reasoning_tokens"],
                estimated_cost_usd=row["estimated_cost_usd"],
                turn_index=row["turn_index"],
                tool_call_count=row["tool_call_count"],
                has_error=bool(row["has_error"]),
                error_fingerprint_hash=row["error_fingerprint_hash"]
            ))
        return events

    def init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_events (
                    event_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    project_hash TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0,
                    estimated_cost_usd REAL DEFAULT 0.0,
                    turn_index INTEGER DEFAULT 1,
                    tool_call_count INTEGER DEFAULT 0,
                    has_error INTEGER DEFAULT 0,
                    error_fingerprint_hash TEXT
                )
            """)
            conn.commit()

    def save_event(self, event: UsageEvent) -> bool:
        with self._get_conn() as conn:
            try:
                conn.execute("""
                    INSERT INTO usage_events (
                        event_id, provider, session_id, project_hash, timestamp,
                        model, input_tokens, output_tokens, cache_read_tokens,
                        cache_write_tokens, reasoning_tokens, estimated_cost_usd,
                        turn_index, tool_call_count, has_error, error_fingerprint_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event.event_id, event.provider, event.session_id, event.project_hash,
                    event.timestamp.isoformat(), event.model, event.input_tokens,
                    event.output_tokens, event.cache_read_tokens, event.cache_write_tokens,
                    event.reasoning_tokens, event.estimated_cost_usd, event.turn_index,
                    event.tool_call_count, 1 if event.has_error else 0,
                    event.error_fingerprint_hash
                ))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_events_for_session(self, session_id: str) -> list[UsageEvent]:
        with self._get_conn(readonly=True) as conn:
            cursor = conn.execute(
                """SELECT * FROM usage_events
                   WHERE session_id = ?
                   ORDER BY timestamp ASC, turn_index ASC""",
                (session_id,),
            )
            return self._rows_to_events(cursor.fetchall())

    def get_events_in_period(self, days: int = 7) -> list[UsageEvent]:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._get_conn(readonly=True) as conn:
            cursor = conn.execute(
                """SELECT * FROM usage_events
                   WHERE timestamp >= ?
                   ORDER BY timestamp ASC, turn_index ASC""",
                (cutoff,),
            )
            return self._rows_to_events(cursor.fetchall())

    def get_all_events(self) -> list[UsageEvent]:
        with self._get_conn(readonly=True) as conn:
            cursor = conn.execute(
                """SELECT * FROM usage_events
                   ORDER BY timestamp ASC, turn_index ASC"""
            )
            return self._rows_to_events(cursor.fetchall())
