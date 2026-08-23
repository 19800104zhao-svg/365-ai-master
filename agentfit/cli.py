import json
from dataclasses import dataclass
from pathlib import Path
import typer

from agentfit.analyzer.scoring import HealthAnalyzer
from agentfit.collector.claude import ClaudeCollector
from agentfit.collector.codex import CodexCollector
from agentfit.storage.database import DatabaseManager

app = typer.Typer(help="AgentFit: 360-Style AI Agent Usage Coach & Diagnostic Tool")


SCAN_EXIT_OK = 0
SCAN_EXIT_NO_DATA = 1
SCAN_EXIT_ERROR = 2


@dataclass
class ScanDiagnostic:
    source: str
    category: str
    message: str
    critical: bool = False


def _format_diagnostics(issues: list[ScanDiagnostic]) -> str:
    if not issues:
        return ""
    lines = ["诊断信息:"]
    for issue in issues:
        level = "错误" if issue.critical else "警告"
        lines.append(f"  [{level}] {issue.source}: {issue.category} - {issue.message}")
    return "\n".join(lines)


def _scan_exit_code(total_events: int, issues: list[ScanDiagnostic]) -> int:
    if any(issue.critical for issue in issues):
        return SCAN_EXIT_ERROR
    if total_events == 0:
        return SCAN_EXIT_NO_DATA
    return SCAN_EXIT_OK


def get_db() -> DatabaseManager:
    db_path = Path.home() / ".agentfit" / "agentfit.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = DatabaseManager(db_path)
    db.init_db()
    return db


def _unique_claude_dirs(dirs: list[Path]) -> list[Path]:
    """去重并去掉父子路径重叠目录，避免递归扫描导致同一日志重复计数。"""
    existing = [d for d in dirs if d.exists()]
    # 先按层级深度从深到浅排序，优先保留更细粒度目录。
    existing.sort(key=lambda p: len(p.parts), reverse=True)

    unique: list[Path] = []
    for d in existing:
        if any(existing_dir.is_relative_to(d) for existing_dir in unique):
            continue
        unique.append(d)

    return unique


def _scan_path(label: str, collector_cls, db: DatabaseManager, scan_dir: Path) -> tuple[int, list[ScanDiagnostic]]:
    if not scan_dir.exists():
        return 0, [ScanDiagnostic(label, "missing", f"路径不存在: {scan_dir}")]

    if not scan_dir.is_dir():
        return 0, [
            ScanDiagnostic(
                label,
                "invalid_source",
                f"路径不是目录: {scan_dir}",
                critical=True,
            )
        ]

    try:
        scanned = collector_cls(scan_dir, db).scan()
    except PermissionError as exc:
        return 0, [
            ScanDiagnostic(
                label,
                "permission_denied",
                f"无权限读取 {scan_dir}: {exc}",
                critical=True,
            )
        ]
    except Exception as exc:
        return 0, [
            ScanDiagnostic(
                label,
                "scan_error",
                f"扫描失败 {scan_dir}: {exc}",
                critical=True,
            )
        ]

    return scanned, []


def _scan_internal(db: DatabaseManager) -> tuple[int, int, list[ScanDiagnostic]]:
    # 实战场景下 Claude 日志分布有明显差异：有些版本在 ~/.claude/sessions，
    # 有些在 ~/.claude/projects 下。
    raw_dirs = [
        Path.home() / ".claude" / "projects",
        Path.home() / ".claude" / "sessions",
        Path.home() / ".claude",
    ]
    claude_dirs = _unique_claude_dirs(raw_dirs)

    c1 = 0
    diagnostics: list[ScanDiagnostic] = []

    if not claude_dirs and not (Path.home() / ".claude").exists():
        diagnostics.append(
            ScanDiagnostic(
                "claude",
                "missing",
                "未发现可扫描的 Claude 日志目录",
            )
        )
    for claude_dir in claude_dirs:
        scanned, issues = _scan_path("claude", ClaudeCollector, db, claude_dir)
        c1 += scanned
        diagnostics.extend(issues)

    codex_dir = Path.home() / ".codex"
    c2, codex_issues = _scan_path("codex", CodexCollector, db, codex_dir)
    diagnostics.extend(codex_issues)

    return c1, c2, diagnostics


@app.command()
def scan():
    """Scan local Claude Code & Codex logs and index into local database."""
    db = get_db()
    c1, c2, diagnostics = _scan_internal(db)
    total = c1 + c2

    typer.echo(f"扫描完成: 已收录 {c1} 条 Claude 记录、{c2} 条 Codex 记录。")
    if diagnostics:
        typer.echo(_format_diagnostics(diagnostics), err=True)

    raise typer.Exit(code=_scan_exit_code(total, diagnostics))


@app.command()
def report(
    json_output: bool = typer.Option(False, "--json"),
    session: str | None = typer.Option(None, "--session", help="只分析某个 session_id"),
    days: int = typer.Option(7, help="分析最近 N 天（按 timestamp）"),
    force_scan: bool = typer.Option(False, "--force-scan", help="先扫描再分析"),
):
    """Generate 360 AI Health Score and diagnostic report."""
    db = get_db()
    scan_exit_code = SCAN_EXIT_OK

    if force_scan:
        c1, c2, scan_diagnostics = _scan_internal(db)
        total = c1 + c2
        scan_exit_code = _scan_exit_code(total, scan_diagnostics)
        typer.echo(f"扫描完成: 已收录 {c1} 条 Claude 记录、{c2} 条 Codex 记录。")
        if scan_diagnostics:
            typer.echo(_format_diagnostics(scan_diagnostics), err=True)

        if scan_exit_code == SCAN_EXIT_ERROR:
            typer.echo("关键扫描错误，使用已有本地数据库继续生成报告。", err=True)

    if session:
        events = db.get_events_for_session(session)
    elif days and days > 0:
        events = db.get_events_in_period(days)
    else:
        events = db.get_all_events()

    if not events:
        typer.echo("⚠️  当前未扫描到可用数据，请先执行 agentfit scan。", err=True)

    analyzer = HealthAnalyzer()
    rep = analyzer.analyze(events)

    if json_output:
        typer.echo(rep.model_dump_json(indent=2))
    else:
        typer.echo(f"🛡️  AI 健康分: {rep.score}/100({rep.tier} 级)")
        typer.echo(f"📊 {rep.percentile_text}")
        typer.echo(f"\n已扫描记录: {len(events)} 条")
        typer.echo("\n--- 最该先修的问题 ---")
        for idx, insight in enumerate(rep.insights, 1):
            typer.echo(f"{idx}. [{insight.title}] -{insight.deduction_points} pts")
            typer.echo(f"   依据: {insight.evidence}")
            typer.echo(f"   修法: {insight.recommendation}\n")

    if scan_exit_code == SCAN_EXIT_ERROR:
        raise typer.Exit(code=SCAN_EXIT_ERROR)
    if not events:
        raise typer.Exit(code=SCAN_EXIT_NO_DATA)


@app.command()
def sync(
    api_url: str = typer.Option(
        None, "--api-url", envvar="AGENTFIT_API_URL",
        help="云端 API 地址 (默认生产环境)",
    ),
    goal: str | None = typer.Option(None, "--goal", help="你的目标 (一次设置,后续沿用)"),
    billing: str | None = typer.Option(
        None, "--billing", help="计费模式: subscription (订阅) 或 api (按量),一次设置沿用"
    ),
    monthly_fee: float | None = typer.Option(
        None, "--monthly-fee", help="订阅月费 (USD),配合 --billing subscription"
    ),
    days: int = typer.Option(7, help="聚合最近 N 天"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只展示将上报的数据,不发送"),
    install_daily: bool = typer.Option(
        False, "--install-daily", help="安装每日 21:00 自动上报 (macOS launchd)"
    ),
):
    """扫描本地日志 → 聚合画像 → 上报云端 → 返回教练报告。

    隐私: 只上报聚合数字,不上报任何提示词内容或明文路径。
    """
    from agentfit import sync as sync_mod

    if install_daily:
        import subprocess

        plist = sync_mod.install_daily_schedule()
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        result = subprocess.run(["launchctl", "load", str(plist)], capture_output=True, text=True)
        if result.returncode == 0:
            typer.echo(f"✓ 已安装每日自动上报 (21:00): {plist}")
            typer.echo(f"  日志: ~/.agentfit/sync.log")
            typer.echo(f"  卸载: launchctl unload {plist} && rm {plist}")
        else:
            typer.echo(f"✗ launchctl 加载失败: {result.stderr}", err=True)
            raise typer.Exit(code=1)
        return

    cfg = sync_mod.load_config()
    changed = False
    had_token = bool(cfg.get("device_token"))
    device_token = sync_mod.get_or_create_device_token(cfg)
    if not had_token:
        changed = True
    if goal:
        cfg["goal"] = goal
        changed = True
    if billing in ("subscription", "api"):
        cfg["billing_mode"] = billing
        changed = True
    if monthly_fee is not None and monthly_fee > 0:
        cfg["monthly_subscription_usd"] = monthly_fee
        changed = True
    if changed:
        sync_mod.save_config(cfg)
    effective_goal = cfg.get("goal")
    effective_api = api_url or cfg.get("api_url") or sync_mod.DEFAULT_API_URL

    # 1. 扫描
    db = get_db()
    c1, c2, diagnostics = _scan_internal(db)
    typer.echo(f"扫描完成: {c1} Claude 事件, {c2} Codex 事件")
    if diagnostics:
        typer.echo(_format_diagnostics(diagnostics), err=True)

    # 2. 聚合
    events = db.get_events_in_period(days)
    if not events:
        typer.echo("⚠️  最近没有可上报的用量数据。", err=True)
        raise typer.Exit(code=SCAN_EXIT_NO_DATA)

    profile = sync_mod.build_profile(
        events,
        goal=effective_goal,
        billing_mode=cfg.get("billing_mode"),
        monthly_subscription_usd=cfg.get("monthly_subscription_usd"),
    )
    profile["device_token"] = device_token
    cost_label = (
        "API 折算价值" if cfg.get("billing_mode") == "subscription" else "成本"
    )
    typer.echo(
        f"画像: 分数 {profile['score']} ({profile['tier']}) · "
        f"{profile['total_tokens_7d']:,} tokens · {cost_label} ${profile['total_cost_7d']} · "
        f"{len(profile['usage_by_model'])} 个模型"
    )

    if dry_run:
        typer.echo(json.dumps(profile, ensure_ascii=False, indent=2, default=str))
        return

    # 3. 上报并展示教练要点
    try:
        report = sync_mod.post_profile(effective_api, profile)
    except Exception as exc:
        typer.echo(f"✗ 上报失败: {exc}", err=True)
        raise typer.Exit(code=SCAN_EXIT_ERROR)

    typer.echo(f"\n🏆 {report.get('beat_ratio_text', '')}")
    typer.echo(f"📋 {report.get('verdict_text', '')}")
    if report.get("value_text"):
        typer.echo(f"💎 {report['value_text']}")
    if report.get("saving_text"):
        typer.echo(f"⚡ {report['saving_text']}")
    for i, item in enumerate(report.get("action_items", []), 1):
        typer.echo(f"  {i}. {item.get('title')} — {item.get('expected_impact')}")
    typer.echo(f"\n查看完整报告 (打开即绑定本机,数据只有你自己看得到): {effective_api}/?t={device_token}")


@app.command()
def optimize(
    apply: bool = typer.Option(
        False, "--apply", help="直接写入 ~/.claude/CLAUDE.md (带标记区块,重跑覆盖,可手动删除)"
    ),
    api_url: str = typer.Option(
        None, "--api-url", envvar="AGENTFIT_API_URL", help="云端 API 地址"
    ),
):
    """一键优化: 把最近体检结果编译成 CLAUDE.md 规则块。

    默认只展示;--apply 幂等写入全局 CLAUDE.md 的标记区块,
    AI 从下一个会话起按新规则工作,下次体检自动验证改善。
    """
    import urllib.request
    import urllib.parse

    from agentfit import sync as sync_mod

    cfg = sync_mod.load_config()
    base = (api_url or cfg.get("api_url") or sync_mod.DEFAULT_API_URL).rstrip("/")
    token = sync_mod.get_or_create_device_token(cfg)
    sync_mod.save_config(cfg)

    try:
        _url = f"{base}/api/v1/coach/optimize?token={urllib.parse.quote(token)}"
        with urllib.request.urlopen(_url, timeout=30) as resp:
            markdown = json.loads(resp.read().decode("utf-8"))["markdown"]
    except Exception as exc:
        typer.echo(f"✗ 获取优化方案失败: {exc}\n  可能还没同步过数据——先运行 agentfit sync,再试一次。", err=True)
        raise typer.Exit(code=1)

    out_path = Path.home() / ".agentfit" / "optimization.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown)

    if not apply:
        typer.echo(markdown)
        typer.echo(f"\n已保存: {out_path}")
        typer.echo("执行 agentfit optimize --apply 一键写入 ~/.claude/CLAUDE.md")
        return

    # --apply: 幂等写入全局 CLAUDE.md 的标记区块(内联实现, CLI 不依赖 cloud)
    from agentfit.apply import apply_to_claude_md

    claude_md = Path.home() / ".claude" / "CLAUDE.md"
    existing = claude_md.read_text() if claude_md.exists() else ""
    backup = claude_md.with_suffix(".md.agentfit-backup")
    if existing:
        backup.write_text(existing)
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    claude_md.write_text(apply_to_claude_md(existing, markdown))

    typer.echo(f"✓ 优化规则已写入 {claude_md} (标记区块,重跑自动更新)")
    if existing:
        typer.echo(f"  备份: {backup}")
    typer.echo("  AI 从下一个会话起按新规则工作;下次 agentfit sync 验证改善")


if __name__ == "__main__":
    app()
