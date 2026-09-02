# 365 AI Master

**AI usage health check for Claude Code & Codex.** Score your AI coding habits, get a step-by-step fix plan, and verify next week that you actually improved.

[中文说明](#中文说明) · [Live](https://master.724.fund) · MIT

---

## What it does

Anthropic's `/insights` tells you what's wrong with how you use Claude Code. This tool closes the loop: it scores you, hands you a fix plan you can paste into `CLAUDE.md`, and re-scores you after you've changed something.

- **One number.** An AI Health Score (0–100) across four dimensions: model routing, time habits, context hygiene, reuse.
- **A ranked issue list** — like a system health check — each with a concrete fix and the expected impact.
- **A fix plan you can apply in one step.** `365aimaster optimize --apply` writes a rules block into your `~/.claude/CLAUDE.md` (or `AGENTS.md` for Codex). Your assistant follows it from the next session.
- **Proof you improved.** Run it daily; the dashboard tracks the before/after.
- **Honest numbers.** Subscription users see quota impact, not fake dollar savings. Rankings count devices, not submissions.

## Privacy

The CLI reads your local Claude Code / Codex logs and uploads **aggregate numbers only**: score, token totals, per-model split, hour-of-day histogram, rule hits, cache hit rate, plus a random device token. It never uploads prompt text, file contents, or file paths. `agentfit/sync.py::build_profile` is the entire upload surface — read it.

Your data is tied to a random device token stored in `~/.agentfit/config.json`. There is no account, no email required, and no "global latest" endpoint on the server.

## Install

Requires Python 3.11+.

```bash
# with pipx (recommended — installs the command globally)
pipx install git+https://github.com/19800104zhao-svg/365-ai-master.git

# or run without installing, using uv
uvx --from git+https://github.com/19800104zhao-svg/365-ai-master.git 365aimaster sync
```

## Use

```bash
365aimaster sync                    # scan logs → score → upload → print your personal report link
365aimaster sync --install-daily    # macOS: re-check every night at 21:00 automatically
365aimaster optimize                # print the fix plan as a CLAUDE.md rules block
365aimaster optimize --apply        # write it into ~/.claude/CLAUDE.md (backs up first, idempotent)
365aimaster report                  # local-only score, no upload
365aimaster sync --dry-run          # see exactly what would be uploaded
```

If you pay per-token (API) instead of a subscription, tell it once so savings are stated in dollars:

```bash
365aimaster sync --billing api
365aimaster sync --billing subscription --monthly-fee 200
```

`agentfit` works as an alias for `365aimaster`.

## Self-hosting the backend

The web dashboard and API live in `cloud/` and `dashboard/`. Deploy with the included `Dockerfile` (it's what runs on Railway).

```bash
pip install -e ".[server]"
uvicorn cloud.main:app --port 8000
```

Environment variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres URL. Required in production (the service refuses to start on Railway without it). |
| `SITE_URL` | Public origin, used for share cards and og:image. |
| `API_KEY` | Enables operator endpoints (export, content pool, billing status). Default key is rejected. |
| `RATE_LIMIT_RPM` | Write-endpoint rate limit per IP (default 60). |
| `STRIPE_*` | Optional; billing stays disabled until set. |

Point the CLI at your instance with `--api-url` or `AGENTFIT_API_URL`.

## Development

```bash
git clone https://github.com/19800104zhao-svg/365-ai-master.git && cd 365-ai-master
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server]"
pytest
```

See `CONTRIBUTING.md`. Security reports: `SECURITY.md`.

---

## 中文说明

**给 Claude Code / Codex 用户的 AI 使用体检。** 给你的 AI 使用习惯打分,给一份能直接照做的修复方案,下周再测一次验证你真的改善了。

像 360 安全卫士体检电脑那样体检你的 AI 用法:

- **一个分数**:AI 健康分(0–100),四个维度——模型路由、时间习惯、上下文卫生、沉淀复用
- **问题清单**:每条带修复方案和预期收益
- **一键修复**:`365aimaster optimize --apply` 把规则写进 `~/.claude/CLAUDE.md`(Codex 用 `AGENTS.md`),AI 下次对话起自动照做
- **验证改善**:每天自动体检,仪表板记录前后对比
- **数字诚实**:订阅用户看到的是额度释放而不是假的"省了多少钱";排名按设备数算,不按提交次数

### 隐私

只上传聚合数字(分数、token 总量、模型分布、24 小时分布、规则命中、缓存命中率)和一个随机设备令牌。**不上传任何提示词内容、文件内容、文件路径。** 上传的全部内容就是 `agentfit/sync.py` 里的 `build_profile` 函数,可以自己看。无需注册账号。

### 安装与使用

需要 Python 3.11+。

```bash
pipx install git+https://github.com/19800104zhao-svg/365-ai-master.git
365aimaster sync
```

跑完会打印一条专属链接,打开就是你的报告。加 `--install-daily` 每晚自动体检(macOS)。

## License

MIT © 2026 Kenichi
