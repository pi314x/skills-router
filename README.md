# skill-router

**An MCP server that serves *skills* — procedures — rather than domain tools.**

Tool servers are everywhere. The procedure that says *how to use* those tools —
which to call, in what order, and how to read the result without fooling yourself —
usually lives in a prompt somebody pasted, or nowhere. This holds the procedures, and
routes a task to the right one.

```
┌──────────────┐   list/route/get_skill   ┌──────────────────┐
│              │ ───────────────────────► │  skill-router    │  procedures
│  MCP client  │                          └──────────────────┘
│  (the model) │   whatever the skill uses┌──────────────────┐
│              │ ───────────────────────► │  your domain     │  capabilities
└──────────────┘                          │  MCP server(s)   │
                                          └──────────────────┘
```

It **composes with** your existing MCP servers, it does not wrap them. Three tools:

| Tool | Returns | Cost |
|---|---|---|
| `list_skills()` | the index — name + one-line description | ~150 bytes per skill |
| `route_skill(task)` | a lexical shortlist for a task string | no model call, no network |
| `get_skill(name)` | the full procedure | ~2 KB, once, for the one chosen |

That split is the whole design — **progressive disclosure**. Only the index sits in
context; a body is fetched when it is wanted. Adding a skill costs a line, not a page.

## Quick start

```bash
pip install -r requirements-server.txt
python skill_server.py --check          # validate the catalog, no listener — CI-safe
python skill_server.py                  # http://127.0.0.1:8001/mcp

python skills.py --list                 # the index a model would see (offline, free)
python skills.py --route "a coin alerted at 6.2 — real?"
python skills.py --show triage-alert
```

Connect it to Claude Code alongside whatever else you use:

```bash
claude mcp add --transport http skills http://localhost:8001/mcp
```

## Writing a skill

A skill is a Markdown file. Frontmatter, then the procedure:

```markdown
---
name: triage-alert
description: A coin is scoring high on the live radar right now — decide whether real
  accumulation is forming or it is a single-signal false alarm.
keywords: alert, flag, triage, false alarm, accumulation, orderbook, right now
tools: get_radar, get_pressure_flags, get_weights, get_regime, get_klines
---

Most flags are noise. The job is to reject fast and cheaply, not to find a reason to
believe. Work in this order and stop at the first step that rejects.

1. **Read the board, not the coin.** …
```

Data, not code — adding one touches no Python. `tools:` names what a **client** needs
from its *other* MCP servers; this server never calls them. `/info` reports the union
as `client_must_serve`, because a procedure step naming a tool nobody serves fails
**silently**: the model follows the instruction and invents the call.

**The `description` line is the entire routing surface.** It is worth more editing
time than the procedure is — see below.

## Two properties worth defending

**The router can return nothing.** `route_skill` returns `[]` when nothing clears the
score floor, and every caller treats that as *use the raw tools*. A router that always
returns its best guess will confidently apply a trading procedure to a question about
the weather, and a procedure reads like authority.

**Routing scores metadata only, never skill bodies.** A body is prose for the model to
follow; scoring it makes every skill a weak match for any word its instructions happen
to use — "explain the python garbage collector" once matched the skill whose procedure
says *"Explain why the size is small"*. Both properties are pinned by tests.

## The routing ladder

Four tiers, cheapest first. Move down only when the tier above stops working.

1. **Native tool calling.** Hand the model every tool and let it pick. Right up to
   ~15–20 tools, and it needs no router at all. What it does not solve is *procedure*.
2. **Index + fetch.** This project's default. Descriptions in the system prompt; the
   model calls `get_skill(name)` for the one it picks. The tier that scales.
3. **Pre-filter, then fetch.** `route_skill(task)` ranks before the model sees
   anything — BM25-ish, deterministic, ~0 ms. Swap in embeddings when the catalog
   reaches a few hundred and paraphrase starts to matter; the interface is unchanged.
   The catch is that it commits before the model has reasoned, and short tasks go
   near-tied: *"a coin alerted at 6.2 — real?"* scores `hunt-targets` 1.733 against
   `triage-alert` 1.723, because "coin" and "alert" pull different ways. Add the word
   "noise" and `triage-alert` wins 4.03 to 1.73. That is why tier 2 is the default —
   it shows the model the index and lets it choose.
4. **Server-side tool search.** On Claude, `defer_loading: true` plus
   `tool_search_tool_bm25_20251119`: schemas load on demand and are *appended*, so the
   prompt cache survives. `llm_router.py --tool-search`.

## Driving it from a model

`llm_router.py` is the reference client and the proof the design is provider-neutral:
MCP is the registry, and the only vendor-specific code is ~40 lines of schema
translation. It connects to several servers and merges their catalogs.

```bash
export USE_AI=true ANTHROPIC_API_KEY=...
python llm_router.py "a coin just alerted at 6.2 — real or noise?"
python llm_router.py --mcp-url http://127.0.0.1:8001/mcp \
                     --mcp-url http://127.0.0.1:9000/mcp "..."     # + a domain server
python llm_router.py --provider openai --routing prefilter "..."
```

| | Claude | OpenAI |
|---|---|---|
| Tool shape | `{name, description, input_schema}` | `{type:"function", function:{…, parameters}}` |
| Results | `tool_result` blocks, **all in one** user message | one `{role:"tool", tool_call_id}` message each |
| Talks to MCP directly | yes — `mcp_servers` + `mcp_toolset` (`--mode connector`) | no; you run the client |
| Server-side tool routing | yes — tool search + `defer_loading` | no; use tier 3 |

## Java build

[`java-mcp-server/`](java-mcp-server/README.md) is the same server on Spring Boot 4.1
+ Spring AI 2.0 — **File → Import → Maven → Existing Maven Projects**, then Run As.
It reads the same `skills/*.md`, and its `SkillRouter` is a port of `skills.py` pinned
to the same six acceptance cases: both implementations score an identical task
identically.

## What is in here

| Path | Role |
|---|---|
| `skills/*.md` | The catalog. Data, not code |
| `skills.py` | Loader, validator, index, lexical router. **Stdlib only** — no MCP, no provider, no network |
| `skill_server.py` | The MCP server (Python SDK 2.x): 3 tools, `skill://` resources, a `use_skill` prompt |
| `llm_router.py` | Reference client — Claude or OpenAI, three routing strategies, many servers |
| `java-mcp-server/` | The same server in Java/Spring Boot |
| `tests/test_skills.py` | Offline. The six routing cases are acceptance criteria |
| `docs/MCP_SERVER.md` | Transports, auth, TLS, Docker/systemd, per-client config |

The shipped catalog is six skills for a [crypto pump-and-dump detection
toolkit](https://github.com/pi314x/pump-and-dump-toolkit) — real ones, kept as worked
examples of what a good procedure looks like (each carries that project's own honest
caveats rather than laundering its tools into confident advice). Point `SKILLS_DIR` at
your own directory to serve a different catalog; nothing else changes.

## What this does not fix

- **Routing is not grounding.** Loading the right procedure does not stop a model
  inventing a value. That is what tool results and a "never invent" system prompt are
  for, and it still needs checking.
- **A skill is only as honest as its text.** A procedure written without the caveats
  will launder the same tools into confident advice.
- **Adding skills is not free.** Each costs an index line on every request and, more
  importantly, adds a way for the router to be wrong. Six well-separated skills beat
  twenty overlapping ones.

MIT licensed.
