import json
from datetime import datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from agentfit.cli import app
from agentfit.storage.database import DatabaseManager
from agentfit.models.events import UsageEvent

runner = CliRunner()


def _seed_events(db: DatabaseManager):
    now = datetime.now()

    events = [
        UsageEvent(
            event_id="evt_recent_1",
            provider="claude",
            session_id="session_alpha",
            project_hash="project_one",
            timestamp=now - timedelta(hours=1),
            model="claude-3-5-sonnet",
            input_tokens=120,
            output_tokens=40,
            cache_read_tokens=10,
            cache_write_tokens=5,
            turn_index=1,
            estimated_cost_usd=0.003,
        ),
        UsageEvent(
            event_id="evt_recent_2",
            provider="claude",
            session_id="session_alpha",
            project_hash="project_one",
            timestamp=now - timedelta(hours=2),
            model="claude-3-5-haiku",
            input_tokens=80,
            output_tokens=30,
            cache_read_tokens=8,
            cache_write_tokens=2,
            turn_index=2,
            estimated_cost_usd=0.002,
        ),
        UsageEvent(
            event_id="evt_old_1",
            provider="codex",
            session_id="session_beta",
            project_hash="project_two",
            timestamp=now - timedelta(days=14),
            model="codex-v1",
            input_tokens=999,
            output_tokens=333,
            cache_read_tokens=0,
            cache_write_tokens=0,
            turn_index=1,
            estimated_cost_usd=0.001,
        ),
        UsageEvent(
            event_id="evt_beta_1",
            provider="codex",
            session_id="session_beta",
            project_hash="project_two",
            timestamp=now - timedelta(hours=3),
            model="claude-3-5-sonnet",
            input_tokens=60,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=1,
            turn_index=1,
            estimated_cost_usd=0.002,
        ),
    ]

    for event in events:
        assert db.save_event(event) is True



def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "AgentFit" in result.output
    assert "scan" in result.output
    assert "report" in result.output


def test_cli_report_default_uses_recent_window(tmp_path, monkeypatch):
    db_path = tmp_path / "test_agentfit.db"
    db = DatabaseManager(db_path)
    db.init_db()
    _seed_events(db)

    monkeypatch.setattr("agentfit.cli.get_db", lambda: db)

    result = runner.invoke(app, ["report", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)

    # 最近 7 天应仅包含 session_alpha 的2条 + session_beta 的最近1条（排除14天前事件）
    assert payload["total_tokens_7d"] == (120 + 40) + (80 + 30) + (60 + 20)



def test_cli_report_session_filter_and_days_all_events(tmp_path, monkeypatch):
    db_path = tmp_path / "test_agentfit.db"
    db = DatabaseManager(db_path)
    db.init_db()
    _seed_events(db)

    monkeypatch.setattr("agentfit.cli.get_db", lambda: db)

    # 仅 session_alpha
    alpha_result = runner.invoke(app, ["report", "--json", "--session", "session_alpha"])
    assert alpha_result.exit_code == 0
    alpha_payload = json.loads(alpha_result.output)
    assert alpha_payload["total_tokens_7d"] == (120 + 40) + (80 + 30)

    # --days 0 应读取全量（不按窗口）
    all_result = runner.invoke(app, ["report", "--json", "--days", "0"])
    assert all_result.exit_code == 0
    all_payload = json.loads(all_result.output)
    assert all_payload["total_tokens_7d"] == (120 + 40) + (80 + 30) + (999 + 333) + (60 + 20)


def test_cli_scan_accepts_monkeypatched_collectors(tmp_path, monkeypatch):
    class DummyCollector:
        def __init__(self, _log_dir, _db):
            self._db = _db

        def scan(self) -> int:
            return 5

    fake_home = tmp_path / "fake_home"
    (fake_home / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
    (fake_home / ".claude" / "sessions").mkdir(parents=True, exist_ok=True)
    (fake_home / ".claude").mkdir(parents=True, exist_ok=True)
    (fake_home / ".codex").mkdir(parents=True, exist_ok=True)

    # 让 scan 不依赖真实磁盘日志，锁定本地路径为 fake_home
    monkeypatch.setattr("agentfit.cli.ClaudeCollector", DummyCollector)
    monkeypatch.setattr("agentfit.cli.CodexCollector", DummyCollector)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    db = DatabaseManager(tmp_path / "agentfit.db")
    db.init_db()
    monkeypatch.setattr("agentfit.cli.get_db", lambda: db)

    result = runner.invoke(app, ["scan"])
    assert result.exit_code == 0
    # 根目录 .claude 命中时会与其子目录重叠，已去重后应仅扫描两层目录
    assert "已收录 10 条 Claude 记录、5 条 Codex 记录。" in result.output


def test_cli_scan_missing_sources_emit_warning_and_exit_code(tmp_path, monkeypatch):
    fake_home = tmp_path / "fake_home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    db = DatabaseManager(tmp_path / "agentfit.db")
    db.init_db()
    monkeypatch.setattr("agentfit.cli.get_db", lambda: db)

    result = runner.invoke(app, ["scan"])
    assert result.exit_code == 1
    assert "诊断信息:" in result.output
    assert "[警告] claude: missing" in result.output


def test_cli_report_force_scan_error_bubbles_exit_code(tmp_path, monkeypatch):
    class DummyCollector:
        def __init__(self, _log_dir, _db):
            self._db = _db

        def scan(self) -> int:
            return 1

    class ErrorCollector:
        def __init__(self, _log_dir, _db):
            pass

        def scan(self) -> int:
            raise RuntimeError("boom")

    fake_home = tmp_path / "fake_home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".codex").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    monkeypatch.setattr("agentfit.cli.ClaudeCollector", DummyCollector)
    monkeypatch.setattr("agentfit.cli.CodexCollector", ErrorCollector)

    base_db = DatabaseManager(tmp_path / "agentfit.db")
    base_db.init_db()
    monkeypatch.setattr("agentfit.cli.get_db", lambda: base_db)

    result = runner.invoke(app, ["report", "--json", "--force-scan"])
    assert result.exit_code == 2
    assert "[错误] codex: scan_error" in result.output


def test_cli_report_force_scan_and_json(tmp_path, monkeypatch):
    class DummyCollector:
        def __init__(self, _log_dir, _db):
            self._db = _db

        def scan(self) -> int:
            return 1

    # 先放入一条数据，确认 --force-scan 会触发扫描行为（会输出扫描日志再输出 report）
    base_db = DatabaseManager(tmp_path / "agentfit.db")
    base_db.init_db()
    from datetime import datetime

    base_db.save_event(
        UsageEvent(
            event_id="evt_seed",
            provider="claude",
            session_id="session_seed",
            project_hash="project_seed",
            timestamp=datetime.now(),
            model="claude-3-5-sonnet",
            input_tokens=1,
            output_tokens=2,
            cache_read_tokens=0,
            cache_write_tokens=0,
            turn_index=1,
            estimated_cost_usd=0.001,
        )
    )

    monkeypatch.setattr("agentfit.cli.ClaudeCollector", DummyCollector)
    monkeypatch.setattr("agentfit.cli.CodexCollector", DummyCollector)
    monkeypatch.setattr("agentfit.cli.get_db", lambda: base_db)

    result = runner.invoke(app, ["report", "--json", "--force-scan"])
    assert result.exit_code == 0
    assert "扫描完成:" in result.output
    payload = json.loads(result.output.split("\n", 1)[1])
    assert "score" in payload

