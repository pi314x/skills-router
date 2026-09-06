# AGENTS.md

Guidance for agents (and humans) working in this repo.

## What this is

An MCP server (`skill_server.py`) that serves *procedures* (skills, as Markdown
files under `skills/`) rather than tools. `skills.py` is the stdlib-only loader,
validator and lexical router — no network, no MCP, no LLM provider. `llm_router.py`
is the reference client: it drives the catalog from Claude, OpenAI, or OpenRouter
and is the only file here that spends money (gated behind `USE_AI=true`).

## Setup

```bash
pip install -r requirements-server.txt   # server + llm_router.py deps
pip install pytest
```

## Tests

```bash
python3 -m pytest tests/ -q
```

All tests are offline — no network, no real API keys, no LLM calls. Tests for
`llm_router.py`'s provider wiring (`tests/test_llm_router.py`) fake the
`openai.AsyncOpenAI` client to check what would have been sent (base URL, API
key, model), not to actually call a model. Keep new provider/transport tests
offline the same way: never let a test spend money or require network access.

`tests/test_skills.py` treats the six routing test cases as acceptance criteria
for the lexical router — a new skill that steals one of those matches is a real
regression, not a flaky test.

## Adding an LLM provider to llm_router.py

- If the provider speaks the OpenAI Chat Completions API (OpenRouter, most
  gateways do), reuse `run_openai` with a different `base_url`/`api_key`/`model`
  rather than writing a new request/response translation — see `run_openrouter`
  for the pattern.
- Only add real translation code (like `to_anthropic_tools`/`to_openai_tools`)
  for a genuinely different wire shape.
- Wire the provider into `pick_provider` (env-var auto-detection) and the
  `--provider` argparse choices, and check whether Claude-only flags
  (`--mode connector`, `--tool-search`) should reject it.

## Conventions

- No comments explaining *what* code does — only *why*, for non-obvious
  constraints or workarounds.
- Don't add abstractions, config flags, or error handling for cases that can't
  happen; keep changes minimal and scoped to the task.
- Never commit secrets — `.env` is gitignored; only `.env.example` (with blank
  values) is tracked.
