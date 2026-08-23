# 365 AI Master

AI usage health check for Claude Code & Codex — score your AI coding habits, get a fix plan, verify you improved.

## What it does

`365 AI Master` scans your local Claude Code / Codex logs, scores how efficiently you're using AI (model routing, context hygiene, caching, task batching), and gives you a step-by-step fix plan. Run it again next week to see if the score moved.

Privacy: only aggregated numbers are uploaded (score, token counts, model mix, hour-of-day distribution). No prompt content, no file paths, ever.

## Quick start

```bash
pip install 365aimaster
365aimaster sync
```

This scans your local logs, uploads an anonymized usage profile, and prints a link to your personal report.

## Repository layout

- `agentfit/` — the CLI (installable package, name kept for backward compatibility)
- `cloud/` — FastAPI backend: scoring engine, coach/recommendation logic, master content pool
- `dashboard/` — single-file frontend (checkup / recommendations / fix plan / pro pages)
- `tests/` — pytest suite

## Local development

```bash
pip install -e ".[dev,server]"
pytest
uvicorn cloud.main:app --reload
```

## Deployment

The backend deploys to Railway from this repo (`cloud/`, `Dockerfile`, `Procfile`). Push to `main` to trigger a deploy.
