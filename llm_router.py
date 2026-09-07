"""
llm_router.py — drive a skill-router catalog from Claude, OpenAI, OpenRouter or Gemini.

The reference client for this project, and the thing that proves the design is
provider-neutral: MCP is the registry, and the only vendor-specific code is ~40 lines
translating tool schemas and results between the MCP shape and each vendor's.

It connects to **one or more** MCP servers and merges their catalogs — that is the
point. skill-router serves the procedures; your domain servers serve the capabilities
the procedures call for. Neither knows about the other; the client is where they meet.

    ┌──────────────┐   list/route/get_skill   ┌──────────────────┐
    │ llm_router   │ ───────────────────────► │  skill-router    │  procedures
    │  Claude      │                          └──────────────────┘
    │  OpenAI      │   whatever the skill uses┌──────────────────┐
    └──────────────┘ ───────────────────────► │  domain server(s)│  capabilities
                                              └──────────────────┘

Three routing strategies (`--routing`), cheapest first:

  prefilter  The server's lexical matcher picks before the model runs, and the chosen
             procedure is preloaded into the system prompt. No model call spent on
             routing at all. Best when the task string is descriptive.
  model      The skill *index* (one line each) goes in the system prompt; the model
             calls get_skill(name) for the one it wants. Progressive disclosure — the
             default, and the one that scales as the catalog grows.
  none       Raw tools, no skills. The baseline to compare the others against.

Two Claude-only transports worth knowing about (`--mode`):

  local      We are the MCP client and run the tool loop ourselves. Works on both
             providers, and the only option for OpenAI today.
  connector  Claude connects to the MCP URLs itself (`mcp_servers` + `mcp_toolset`).
             No loop code, no MCP client — the API does it. Add --tool-search and the
             server-side BM25 tool search loads schemas on demand too, so even the
             tool list stops costing context.

Requires a running server (`python skill_server.py`) and
`pip install -r requirements-server.txt`. Gated behind USE_AI — this is the only file
here that spends money, so it stays off until you opt in.

    export USE_AI=true ANTHROPIC_API_KEY=...
    python llm_router.py "a coin just alerted at 6.2 — real or noise?"
    python llm_router.py --mcp-url http://127.0.0.1:8001/mcp \
                         --mcp-url http://127.0.0.1:9000/mcp "..."   # + a domain server
    python llm_router.py --provider openai --routing prefilter "..."
    python llm_router.py --provider openrouter "..."   # OPENROUTER_API_KEY; any model OpenRouter serves
    python llm_router.py --provider gemini "..."       # GEMINI_API_KEY, via Gemini's OpenAI-compat endpoint
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MCP_URLS = [u.strip() for u in
                    os.getenv("MCP_URLS", "http://127.0.0.1:8001/mcp").split(",") if u.strip()]
MCP_TOKEN = os.getenv("MCP_TOKEN", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")

# Gateways we pass an explicit key to.  Claude and OpenAI are absent on purpose:
# their SDKs read their own env var and raise their own error when it is missing.
PROVIDER_KEYS = {"openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_API_KEY"}

# The skill-routing tools are meta: they describe the catalog rather than the market.
# Kept out of any deferred-loading set, because they are the entry point to everything
# else — defer them and the model has no way to discover that skills exist.
META_TOOLS = ("list_skills", "route_skill", "get_skill")

SYSTEM_BASE = (
    "You are an agent driving a set of MCP tools.\n"
    "Ground every claim in a tool result: never invent a value, an identifier or a statistic.\n"
    "If the tools do not support a claim, say what is missing instead of filling the gap.\n"
    "A skill's procedure is authoritative about HOW to interpret what the tools return — "
    "follow it rather than substituting your own judgement about what the numbers mean."
)


def ai_enabled() -> bool:
    """Opt-in switch — no LLM call happens without it.

    Everything else in this project is free and offline; this file is the one that
    bills. An explicit flag beats discovering that from an invoice."""
    return os.getenv("USE_AI", "false").strip().lower() in ("1", "true", "yes", "on")


def api_key_for(provider: str) -> str:
    """The gateway's API key, or '' when unset.  main() rejects the empty case
    before anything connects, so the run_* wrappers can just read it."""
    return os.getenv(PROVIDER_KEYS[provider], "").strip()


# ---------------------------------------------------------------------------
# MCP side — one connection, one tool catalog, both providers read from it
# ---------------------------------------------------------------------------

class ToolSpec:
    """A provider-neutral tool: exactly what MCP hands us, before translation."""

    __slots__ = ("name", "description", "schema")

    def __init__(self, name: str, description: str, schema: Dict[str, Any]):
        self.name = name
        self.description = description or ""
        self.schema = schema or {"type": "object", "properties": {}}


async def fetch_tools(session) -> List[ToolSpec]:
    res = await session.list_tools()
    return [ToolSpec(t.name, t.description or "", t.input_schema or {}) for t in res.tools]


async def call_tool(session, name: str, args: Dict[str, Any]) -> str:
    """Call an MCP tool and return text for the model.

    Prefers `structured_content` (the tool's declared output schema, present on
    every tool here but get_skill) over the text blocks, so the model sees canonical
    JSON rather than whatever the server chose to render.

    Tool errors come back as text rather than raised: the model handles "that file
    does not exist yet" far better than the loop does, and a raised exception loses
    the turn's work."""
    try:
        result = await session.call_tool(name, args or {})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}", "tool": name})

    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return json.dumps(structured, indent=2)
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        parts.append(text if text is not None else str(block))
    body = "\n".join(parts) if parts else json.dumps({"ok": True, "tool": name, "content": []})
    if getattr(result, "is_error", False):
        return json.dumps({"error": "tool reported failure", "tool": name, "detail": body})
    return body


class _BearerTransport:
    """Streamable HTTP transport carrying an Authorization header.

    `Client(url)` builds its own transport with no way to add headers, so a
    token-protected server needs this one instead. A Transport is just an async
    context manager yielding (read, write) streams, which is all this is."""

    def __init__(self, url: str, token: str):
        self._url, self._token, self._cm = url, token, None

    async def __aenter__(self):
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        http = create_mcp_http_client(headers={"Authorization": f"Bearer {self._token}"})
        self._cm = streamable_http_client(self._url, http_client=http)
        return await self._cm.__aenter__()

    async def __aexit__(self, *exc):
        return await self._cm.__aexit__(*exc)


def mcp_client(url: str):
    """Open a session against one MCP server, with auth when MCP_TOKEN is set."""
    from mcp import Client

    return Client(_BearerTransport(url, MCP_TOKEN) if MCP_TOKEN else url)


class Fleet:
    """Several MCP servers presented to the model as one tool catalog.

    skill-router serves the procedures; the domain servers serve the capabilities
    those procedures call for. Neither knows about the other — this is where they
    meet, which is the whole reason the skill layer is its own server rather than a
    wrapper around somebody's tools.

    Tool names are assumed unique across servers. On a collision the first server
    listed wins and the loser is reported once at startup rather than silently
    shadowed: a model calling what it thinks is your `search` and reaching someone
    else's is the kind of bug that looks like a bad answer, not a routing fault."""

    def __init__(self) -> None:
        self.sessions: List[Any] = []
        self.owner: Dict[str, Any] = {}          # tool name -> the session serving it
        self.specs: List[ToolSpec] = []
        self.collisions: List[str] = []

    async def add(self, session) -> None:
        self.sessions.append(session)
        for spec in await fetch_tools(session):
            if spec.name in self.owner:
                self.collisions.append(spec.name)
                continue
            self.owner[spec.name] = session
            self.specs.append(spec)

    @property
    def skill_session(self):
        """The session serving the skill tools, or None if no server offers them."""
        return self.owner.get("route_skill") or self.owner.get("list_skills")

    async def call(self, name: str, args: Dict[str, Any]) -> str:
        session = self.owner.get(name)
        if session is None:
            # The model asked for something no connected server offers. Telling it so
            # beats an exception: it can pick another tool, or say what is missing.
            return json.dumps({"error": f"no connected server serves tool {name!r}",
                               "available": sorted(self.owner)})
        return await call_tool(session, name, args)


async def build_system_prompt(fleet: "Fleet", task: str, routing: str) -> Tuple[str, Optional[str]]:
    """Assemble the system prompt for the chosen routing strategy.

    Returns (system_prompt, preloaded_skill_name).  This is where the three
    strategies actually differ — the agent loop below is identical for all of them."""
    if routing == "none":
        return SYSTEM_BASE, None

    session = fleet.skill_session
    if session is None:
        # No skill server connected. Say so rather than pretending to route: a routing
        # strategy that silently degrades to "none" hides a misconfigured --mcp-url.
        return (SYSTEM_BASE + "\n\nNo skill server is connected, so no procedures are "
                "available — work from the tools directly."), None

    if routing == "prefilter":
        # Cheapest route: the server's lexical matcher picks before the model runs,
        # so the procedure is already in context on the very first request. No skill
        # discovery turn, no get_skill round trip.
        shortlist = json.loads(await call_tool(session, "route_skill", {"task": task, "top_k": 1}))
        matches = shortlist.get("matches") or []
        if not matches:
            # An honest miss. Forcing the least-bad procedure onto an unrelated task
            # is worse than having none, so fall through to raw tools.
            return SYSTEM_BASE + "\n\nNo skill matches this task — use the tools directly.", None
        name = matches[0]["name"]
        body = await call_tool(session, "get_skill", {"name": name})
        return (
            f"{SYSTEM_BASE}\n\nFollow this procedure for the task:\n\n{body}\n\n"
            "If the task turns out not to fit this procedure, say so and call list_skills.",
            name,
        )

    # routing == "model": progressive disclosure. Only the index is in context; the
    # model spends one tool call to load the body of whichever skill it picks.
    catalog = json.loads(await call_tool(session, "list_skills", {}))
    lines = "\n".join(f"- {s['name']}: {s['description']}" for s in catalog.get("skills", []))
    return (
        f"{SYSTEM_BASE}\n\nAvailable skills (procedures, not tools):\n{lines}\n\n"
        "Pick the ONE whose description matches the task and call get_skill(name) to load its "
        "full procedure BEFORE calling any data tool, then follow it. If none matches, say so "
        "and use the data tools directly.",
        None,
    )


# ---------------------------------------------------------------------------
# Provider adapters — the only vendor-specific code in this file
# ---------------------------------------------------------------------------

def to_anthropic_tools(specs: List[ToolSpec], defer: bool = False) -> List[Dict[str, Any]]:
    """MCP schema -> Anthropic tool.  `input_schema`, flat.

    With defer=True the data tools are marked `defer_loading` so their schemas load
    only when the server-side tool search pulls them in; the meta tools stay eager
    because at least one tool must be, and because they are the way in."""
    out = []
    for s in specs:
        tool: Dict[str, Any] = {"name": s.name, "description": s.description, "input_schema": s.schema}
        if defer and s.name not in META_TOOLS:
            tool["defer_loading"] = True
        out.append(tool)
    return out


def to_openai_tools(specs: List[ToolSpec]) -> List[Dict[str, Any]]:
    """MCP schema -> OpenAI function tool.  `parameters`, nested under `function`."""
    return [
        {"type": "function",
         "function": {"name": s.name, "description": s.description, "parameters": s.schema}}
        for s in specs
    ]


def token_counts(usage) -> Tuple[int, int]:
    """(input, output) from whatever usage object a provider returned.

    Anthropic counts cache hits separately from `input_tokens`, so they are added
    back: all three are prompt tokens that were sent, which is the number a
    routing-cost comparison is about.  The OpenAI shape already folds them into
    `prompt_tokens`.  A gateway that reports no usage at all — some do — counts as
    zero rather than failing a run that otherwise succeeded."""
    if usage is None:
        return 0, 0

    def field(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)

    if getattr(usage, "prompt_tokens", None) is not None:
        return field("prompt_tokens"), field("completion_tokens")
    return (field("input_tokens") + field("cache_read_input_tokens")
            + field("cache_creation_input_tokens")), field("output_tokens")


class Usage:
    """What a run spent, accumulated across turns and providers.

    The point of the three routing strategies is that they cost different amounts
    of context; without a counter that claim is untested folklore, so every loop
    below records each response here and `run()` prints the total."""

    __slots__ = ("input", "output", "turns")

    def __init__(self) -> None:
        self.input = self.output = self.turns = 0

    def record(self, resp, turn: int, verbose: bool) -> None:
        got_in, got_out = token_counts(getattr(resp, "usage", None))
        self.input += got_in
        self.output += got_out
        self.turns += 1
        if verbose:
            print(f"  [{turn}] tokens in={got_in} out={got_out}", file=sys.stderr)

    def report(self, routing: str) -> str:
        return (f"tokens: in={self.input} out={self.output} "
                f"total={self.input + self.output} turns={self.turns} routing={routing}")


async def run_claude(fleet, specs, system: str, task: str, *, usage: Usage, max_turns: int,
                     tool_search: bool, verbose: bool) -> str:
    """Manual agentic loop on the Messages API (we own the MCP transport)."""
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()
    tools: List[Dict[str, Any]] = to_anthropic_tools(specs, defer=tool_search)
    if tool_search:
        # Server-side routing over the tool set, the same idea as route_skill one
        # level down: schemas are appended as they are needed instead of all upfront.
        tools.append({"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"})

    messages: List[Dict[str, Any]] = [{"role": "user", "content": task}]
    for turn in range(max_turns):
        resp = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            system=system,
            tools=tools,
            thinking={"type": "adaptive"},
            messages=messages,
        )
        # Before the pause_turn check: a paused turn was still generated and billed.
        usage.record(resp, turn, verbose)
        if resp.stop_reason == "pause_turn":
            # A server-side tool ran long; re-send to let it continue.
            messages.append({"role": "assistant", "content": resp.content})
            continue

        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            return "".join(b.text for b in resp.content if b.type == "text")

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for c in calls:
            if verbose:
                print(f"  [{turn}] {c.name}({json.dumps(c.input)[:120]})", file=sys.stderr)
            results.append({"type": "tool_result", "tool_use_id": c.id,
                            "content": await fleet.call(c.name, c.input)})
        # All results in ONE user message — splitting them teaches the model to stop
        # calling tools in parallel.
        messages.append({"role": "user", "content": results})

    return f"(stopped after {max_turns} turns without a final answer)"


async def run_claude_connector(mcp_urls: List[str], system: str, task: str, *, usage: Usage,
                               max_turns: int, tool_search: bool, verbose: bool) -> str:
    """No loop, no MCP client: Claude connects to the MCP servers itself.

    Two things bite here. `mcp_servers` alone is a validation error — the matching
    `mcp_toolset` entry in `tools` is the half people forget, one per server. And the
    URLs must be reachable from Anthropic's side, so `127.0.0.1` will not do; this
    mode needs the servers actually published."""
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()
    servers = [{"type": "url", "url": u, "name": f"mcp-{i}"} for i, u in enumerate(mcp_urls)]
    tools: List[Dict[str, Any]] = [{"type": "mcp_toolset", "mcp_server_name": srv["name"]}
                                   for srv in servers]
    if tool_search:
        tools.append({"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"})

    messages: List[Dict[str, Any]] = [{"role": "user", "content": task}]
    for turn in range(max_turns):
        resp = await client.beta.messages.create(
            betas=["mcp-client-2025-11-20"],
            model=CLAUDE_MODEL,
            max_tokens=16000,
            system=system,
            mcp_servers=servers,
            tools=tools,
            thinking={"type": "adaptive"},
            messages=messages,
        )
        usage.record(resp, turn, verbose)
        text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason != "pause_turn":
            return text
        if verbose:
            print("  [connector] pause_turn — resuming", file=sys.stderr)
        messages.append({"role": "assistant", "content": resp.content})
    return f"(stopped after {max_turns} turns without a final answer)"


async def run_openai(fleet, specs, system: str, task: str, *, usage: Usage, max_turns: int,
                     verbose: bool, model: Optional[str] = None, base_url: Optional[str] = None,
                     api_key: Optional[str] = None) -> str:
    """The same loop in OpenAI's shapes: tool_calls out, role='tool' messages back.

    Also drives OpenRouter and Gemini, which speak the identical Chat Completions API
    — only the base URL, API key and model string differ, so there is nothing
    provider-specific to write beyond the wrappers supplying those three."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    tools = to_openai_tools(specs)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    for turn in range(max_turns):
        resp = await client.chat.completions.create(
            model=model or OPENAI_MODEL, messages=messages, tools=tools,
        )
        usage.record(resp, turn, verbose)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""

        messages.append(msg.model_dump(exclude_none=True))
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                # Escaping differs between models; never string-match the raw payload.
                args = {}
            if verbose:
                print(f"  [{turn}] {tc.function.name}({json.dumps(args)[:120]})", file=sys.stderr)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": await fleet.call(tc.function.name, args)})

    return f"(stopped after {max_turns} turns without a final answer)"


async def run_openrouter(fleet, specs, system: str, task: str, *, usage: Usage, max_turns: int,
                         verbose: bool) -> str:
    """OpenRouter over the OpenAI Chat Completions shape — one gateway, many models.

    `OPENROUTER_MODEL` picks which upstream model OpenRouter routes the request to
    (e.g. `anthropic/claude-opus-5`, `openai/gpt-4o-mini`, `meta-llama/llama-3.1-70b-
    instruct`) — see https://openrouter.ai/models for the full catalog."""
    return await run_openai(fleet, specs, system, task, usage=usage, max_turns=max_turns,
                            verbose=verbose, model=OPENROUTER_MODEL,
                            base_url=OPENROUTER_BASE_URL, api_key=api_key_for("openrouter"))


async def run_gemini(fleet, specs, system: str, task: str, *, usage: Usage, max_turns: int,
                     verbose: bool) -> str:
    """Gemini over its OpenAI-compatible endpoint — same reuse of run_openai as
    OpenRouter above. See https://ai.google.dev/gemini-api/docs/openai for the
    compatibility layer and https://ai.google.dev/gemini-api/docs/models for model
    names (`GEMINI_MODEL`, e.g. `gemini-flash-latest`, `gemini-2.5-pro`)."""
    return await run_openai(fleet, specs, system, task, usage=usage, max_turns=max_turns,
                            verbose=verbose, model=GEMINI_MODEL, base_url=GEMINI_BASE_URL,
                            api_key=api_key_for("gemini"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def pick_provider(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return "claude"


async def run(args: argparse.Namespace) -> int:
    provider = pick_provider(args.provider)
    urls: List[str] = args.mcp_url or DEFAULT_MCP_URLS
    usage = Usage()

    if provider == "claude" and args.mode == "connector":
        # Nothing local to drive: the API is the MCP client. We still need the skill
        # catalog for the system prompt, so open short sessions just for that.
        async with connect(urls) as fleet:
            system, _ = await build_system_prompt(fleet, args.task, args.routing)
        answer = await run_claude_connector(
            urls, system, args.task, usage=usage,
            max_turns=args.max_turns, tool_search=args.tool_search, verbose=args.verbose)
        if args.verbose:
            print(usage.report(args.routing), file=sys.stderr)
        print(answer)
        return 0

    async with connect(urls) as fleet:
        system, preloaded = await build_system_prompt(fleet, args.task, args.routing)
        if args.verbose:
            for sess in fleet.sessions:
                info = sess.server_info
                print(f"connected {info.name} v{info.version} "
                      f"(protocol {sess.protocol_version})", file=sys.stderr)
            print(f"provider={provider} tools={len(fleet.specs)} routing={args.routing}"
                  + (f" skill={preloaded}" if preloaded else ""), file=sys.stderr)
        for name in fleet.collisions:
            print(f"warning: tool {name!r} served by more than one server — "
                  f"the first one listed wins", file=sys.stderr)
        if provider == "claude":
            answer = await run_claude(fleet, fleet.specs, system, args.task, usage=usage,
                                      max_turns=args.max_turns,
                                      tool_search=args.tool_search, verbose=args.verbose)
        elif provider == "openrouter":
            answer = await run_openrouter(fleet, fleet.specs, system, args.task, usage=usage,
                                          max_turns=args.max_turns, verbose=args.verbose)
        elif provider == "gemini":
            answer = await run_gemini(fleet, fleet.specs, system, args.task, usage=usage,
                                      max_turns=args.max_turns, verbose=args.verbose)
        else:
            answer = await run_openai(fleet, fleet.specs, system, args.task, usage=usage,
                                      max_turns=args.max_turns, verbose=args.verbose)
    if args.verbose:
        print(usage.report(args.routing), file=sys.stderr)
    print(answer)
    return 0


@asynccontextmanager
async def connect(urls: List[str]):
    """Open every server in `urls` and present them as one Fleet.

    An AsyncExitStack rather than nested `async with`, because the number of servers
    is a runtime value; every session is closed in reverse order on the way out even
    if one of them fails to open."""
    async with AsyncExitStack() as stack:
        fleet = Fleet()
        for url in urls:
            try:
                session = await stack.enter_async_context(mcp_client(url))
            except Exception as e:                       # noqa: BLE001
                raise SystemExit(f"cannot reach MCP server {url}: {type(e).__name__}: {e}") from e
            await fleet.add(session)
        yield fleet


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Drive a skill-router catalog (plus any domain MCP servers) "
                    "from Claude, OpenAI, OpenRouter or Gemini.")
    ap.add_argument("task", help="what you want done, in plain language")
    ap.add_argument("--provider", choices=("claude", "openai", "openrouter", "gemini"),
                    help="default: whichever API key is set (ANTHROPIC, then OPENAI, "
                         "then OPENROUTER, then GEMINI)")
    ap.add_argument("--mode", choices=("local", "connector"), default="local",
                    help="local: we run the MCP client + tool loop.  connector: Claude connects to the MCP URL itself")
    ap.add_argument("--routing", choices=("model", "prefilter", "none"), default="model",
                    help="model: index in context, model calls get_skill (default).  prefilter: lexical shortlist first.  none: raw tools")
    ap.add_argument("--tool-search", action="store_true",
                    help="Claude only: server-side BM25 tool search + deferred tool schemas")
    ap.add_argument("--mcp-url", action="append", metavar="URL",
                    help="MCP server to connect to; repeat for several (skill-router plus "
                         f"your domain servers).  Default: {', '.join(DEFAULT_MCP_URLS)}")
    ap.add_argument("--max-turns", type=int, default=12, help="tool-loop safety cap")
    ap.add_argument("--verbose", action="store_true", help="log each tool call to stderr")
    args = ap.parse_args()

    if not ai_enabled():
        print("USE_AI is not true — refusing to call an LLM.  Set USE_AI=true to opt in "
              "(this is the only script here that spends money).", file=sys.stderr)
        return 2
    provider = pick_provider(args.provider)
    if args.mode == "connector" and provider != "claude":
        print("--mode connector is Claude-only; OpenAI needs --mode local.", file=sys.stderr)
        return 2
    if args.tool_search and provider != "claude":
        print("--tool-search is Claude-only (server-side tool search).", file=sys.stderr)
        return 2
    # Before connecting, not after: a key missing at the far end of run() costs an MCP
    # session and, under --routing prefilter, the routing calls it already spent.
    if provider in PROVIDER_KEYS and not api_key_for(provider):
        print(f"--provider {provider} needs {PROVIDER_KEYS[provider]} set", file=sys.stderr)
        return 2

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except ModuleNotFoundError as e:
        print(f"missing dependency: {e.name}.  pip install -r requirements-server.txt",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
