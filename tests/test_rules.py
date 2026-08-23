from datetime import datetime
from agentfit.models.events import UsageEvent
from agentfit.analyzer.scoring import HealthAnalyzer

def test_context_bloat_rule():
    events = []
    for turn in range(1, 35):
        input_toks = 1000 if turn <= 15 else 8000
        events.append(UsageEvent(
            event_id=f"evt_{turn}",
            provider="claude",
            session_id="sess_bloat",
            project_hash="hash",
            timestamp=datetime.now(),
            model="claude-3-5-sonnet",
            input_tokens=input_toks,
            output_tokens=200,
            turn_index=turn
        ))
    
    analyzer = HealthAnalyzer()
    report = analyzer.analyze(events)
    
    assert report.score < 100
    rule_ids = [i.rule_id for i in report.insights]
    assert "RULE_CONTEXT_BLOAT" in rule_ids
