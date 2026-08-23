import json
from datetime import datetime
from typing import Any

from agentfit.collector.base import BaseCollector
from agentfit.models.events import UsageEvent


class ClaudeCollector(BaseCollector):
    def _extract_usage(self, data: dict[str, Any]) -> dict[str, Any] | None:
        # Claude JSONL lines can vary by version/entrypoint.
        # Accept both legacy "message_start" and raw "message"/"assistant" styles.
        message = None
        if isinstance(data.get("message"), dict):
            message = data.get("message")
        elif data.get("type") in {"assistant", "message"} and data.get("role") == "assistant" and isinstance(data, dict):
            message = data

        if not message:
            return None

        role = message.get("role")
        if role and role != "assistant":
            return None

        usage = message.get("usage") or {}
        if not isinstance(usage, dict):
            return None

        return {
            "message": message,
            "usage": usage,
        }

    def _extract_timestamp(self, data: dict[str, Any]) -> datetime:
        raw = data.get("timestamp")
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now()

    def scan(self) -> int:
        if not self.log_dir.exists():
            return 0

        scanned = 0
        for log_file in self.log_dir.glob("**/*.jsonl"):
            session_id = log_file.stem
            with open(log_file, "r", encoding="utf-8") as f:
                turn = 1
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                        event_type = data.get("type")
                        if event_type not in {"message_start", "message", "assistant"}:
                            continue

                        payload = self._extract_usage(data)
                        if not payload:
                            continue

                        msg = payload["message"]
                        usage = payload["usage"]
                        model = msg.get("model") or "claude-3-5-sonnet"
                        msg_id = msg.get("id") or data.get("id") or f"{session_id}_{turn}"

                        cwd = data.get("cwd") or msg.get("cwd") or str(log_file.parent)
                        project_hash = self.hash_project_path(cwd)

                        event = UsageEvent(
                            event_id=f"claude_{msg_id}",
                            provider="claude",
                            session_id=session_id,
                            project_hash=project_hash,
                            timestamp=self._extract_timestamp(data),
                            model=model,
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                            cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
                            turn_index=turn,
                            estimated_cost_usd=0.003,
                        )

                        if self.db.save_event(event):
                            scanned += 1
                        turn += 1
                    except Exception:
                        continue
        return scanned
