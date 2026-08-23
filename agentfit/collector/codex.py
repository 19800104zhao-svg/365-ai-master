import json
from datetime import datetime
from pathlib import Path
from agentfit.collector.base import BaseCollector
from agentfit.models.events import UsageEvent

class CodexCollector(BaseCollector):
    def scan(self) -> int:
        if not self.log_dir.exists():
            return 0
        scanned = 0
        for log_file in self.log_dir.glob("**/*.json"):
            session_id = log_file.stem
            project_hash = self.hash_project_path(str(log_file.parent))
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "usage" in data:
                        u = data["usage"]
                        event = UsageEvent(
                            event_id=f"codex_{session_id}_{data.get('id', '1')}",
                            provider="codex",
                            session_id=session_id,
                            project_hash=project_hash,
                            timestamp=datetime.now(),
                            model=data.get("model", "codex-v1"),
                            input_tokens=u.get("prompt_tokens", 0),
                            output_tokens=u.get("completion_tokens", 0),
                            turn_index=1
                        )
                        if self.db.save_event(event):
                            scanned += 1
            except Exception:
                continue
        return scanned
