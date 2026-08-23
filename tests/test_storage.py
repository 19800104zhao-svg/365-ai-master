import pytest
from datetime import datetime
from agentfit.models.events import UsageEvent
from agentfit.storage.database import DatabaseManager

def test_database_init_and_event_insertion(tmp_path):
    db_path = tmp_path / "test_agentfit.db"
    db = DatabaseManager(db_path)
    db.init_db()

    event = UsageEvent(
        event_id="evt_001",
        provider="claude",
        session_id="sess_123",
        project_hash="hash_abc",
        timestamp=datetime(2026, 7, 31, 10, 0, 0),
        model="claude-3-5-sonnet-20241022",
        input_tokens=1000,
        output_tokens=150,
        cache_read_tokens=500,
        cache_write_tokens=100,
        estimated_cost_usd=0.015,
        turn_index=1,
        tool_call_count=2,
        has_error=False
    )
    
    inserted = db.save_event(event)
    assert inserted is True
    
    # Re-inserting identical event_id must return False (deduplication)
    inserted_again = db.save_event(event)
    assert inserted_again is False

    events = db.get_events_for_session("sess_123")
    assert len(events) == 1
    assert events[0].event_id == "evt_001"
