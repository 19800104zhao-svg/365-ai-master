import json
import pytest
from pathlib import Path
from agentfit.collector.claude import ClaudeCollector
from agentfit.collector.codex import CodexCollector
from agentfit.storage.database import DatabaseManager

def test_claude_collector_parses_jsonl(tmp_path):
    log_dir = tmp_path / "claude_logs"
    log_dir.mkdir()
    session_file = log_dir / "session_abc.jsonl"
    
    log_file_content = [
        {"type": "message_start", "message": {"id": "msg_001", "model": "claude-3-5-sonnet-20241022", "usage": {"input_tokens": 1200, "output_tokens": 0, "cache_read_input_tokens": 800}}},
        {"type": "message_delta", "usage": {"output_tokens": 150}}
    ]
    with open(session_file, "w") as f:
        for entry in log_file_content:
            f.write(json.dumps(entry) + "\n")

    db = DatabaseManager(tmp_path / "agentfit.db")
    db.init_db()

    collector = ClaudeCollector(log_dir=log_dir, db=db, local_salt="salt123")
    scanned_count = collector.scan()
    assert scanned_count >= 1

    events = db.get_events_for_session("session_abc")
    assert len(events) == 1
    assert events[0].input_tokens == 1200
    assert events[0].cache_read_tokens == 800
