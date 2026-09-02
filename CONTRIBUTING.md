# Contributing

Thanks for looking. This is a small project; the bar is "tests pass and the change is easy to review".

## Setup

```bash
git clone https://github.com/19800104zhao-svg/365-ai-master.git
cd 365-ai-master
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server]"
pytest
```

## Where things live

| Path | What |
|---|---|
| `agentfit/` | CLI. Scans local Claude Code / Codex logs, scores them, uploads an anonymized profile. |
| `cloud/` | FastAPI backend: scoring rules, coach/recommendation engine, curated content pool. |
| `dashboard/index.html` | The whole web UI. One file on purpose — no build step. |
| `tests/` | pytest. Every rule and every endpoint has a test; please keep it that way. |

## Rules that matter here

- **Privacy is the product.** The CLI must never upload prompt text, file contents, or file paths. Aggregate numbers only. Tests in `tests/test_identity.py` enforce this — don't weaken them.
- **Numbers must be defensible.** Any figure shown to a user (savings, percentile, rank) has to be computed from real data with an honest denominator. If a number is an estimate, say so in the UI.
- **Curated content lives in code.** Recommendations shown in the app come from `cloud/master.py`'s `CONTENT_POOL` and go through code review. Every entry needs a real source and a stated reason to trust it. No marketing.

## Pull requests

- One change per PR, with a test.
- Run `pytest` before pushing. CI runs on 3.11 and 3.12.
- Commit messages: say what changed and why, in whichever language you write best.
