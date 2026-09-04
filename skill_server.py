"""
skill_server.py — an MCP server that serves *skills*, not domain tools.

A **tool** is one capability with a JSON schema (`get_radar`, `create_ticket`). A
**skill** is the procedure over several of them: which to call, in what order, and how
to read the result without fooling yourself. Tool servers are everywhere; the
procedure usually lives in a prompt somebody pasted, or nowhere.

This server holds the procedures. It exposes exactly three tools:

    list_skills()        the index — name + one-line description. Cheap, always affordable.
    route_skill(task)    a lexical shortlist for a task string. No model call, no network.
    get_skill(name)      the full procedure, once the model has chosen.

That split is the whole design — **progressive disclosure**. Only the index sits in
context (~150 bytes per skill); a body is fetched when it is wanted. Adding a skill
costs a line, not a page, so the catalog can grow without every request paying for it.

## It composes, it does not replace

This server runs *alongside* your domain MCP servers, it does not wrap them:

    ┌──────────────┐   list/route/get_skill   ┌──────────────────┐
    │              │ ───────────────────────► │  skill-router    │  procedures
    │  MCP client  │                          └──────────────────┘
    │  (the model) │   get_radar, get_klines  ┌──────────────────┐
    │              │ ───────────────────────► │  your domain     │  capabilities
    └──────────────┘                          │  MCP server(s)   │
                                              └──────────────────┘

A skill's `tools:` frontmatter names tools the **client** is expected to have from
those other servers — this one never calls them. `--check` and `/info` report that as
`client_must_serve`, so an operator can see what a catalog assumes before a model
discovers it the hard way: a skill step naming a tool nobody serves fails silently,
because the model follows the procedure and invents the call.

    python skill_server.py                    # http://127.0.0.1:8001/mcp
    python skill_server.py --check            # validate, no listener — CI-safe
    python skill_server.py --transport stdio  # a desktop client spawns us

Setup, auth, TLS, Docker and per-client config: docs/MCP_SERVER.md.
"""

from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations

import skills as skills_mod

SERVER_VERSION = "1.0.0"

ROOT = Path(__file__).parent
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", str(ROOT / "skills")))

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", os.getenv("PORT", "8001")))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")
MCP_TOKEN = os.getenv("MCP_TOKEN", "").strip()


def _csv_env(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name, "").strip()
    return [p.strip() for p in raw.split(",") if p.strip()] or default


def transport_security() -> TransportSecuritySettings:
    """Host/Origin allow-lists for the HTTP transports.

    MCP's spec calls out DNS-rebinding explicitly: a local server on a known port is
    reachable from any page the user opens unless Host and Origin are checked. So the
    defaults are the closed ones, and publishing the server means naming the hostname
    clients reach it by."""
    hosts = _csv_env("MCP_ALLOWED_HOSTS",
                     ["127.0.0.1", "localhost", f"127.0.0.1:{MCP_PORT}", f"localhost:{MCP_PORT}"])
    origins = _csv_env("MCP_ALLOWED_ORIGINS",
                       ["http://127.0.0.1", "http://localhost",
                        f"http://127.0.0.1:{MCP_PORT}", f"http://localhost:{MCP_PORT}"])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins,
    )


# ---------------------------------------------------------------------------
# The catalog.  Loaded once at startup: skills change when a human edits a file,
# not per request, and re-reading the directory on every list_skills call would be
# work done for nobody.  A malformed file fails loudly here rather than becoming a
# routing hole nobody can see.
# ---------------------------------------------------------------------------
try:
    SKILLS = skills_mod.load_skills(SKILLS_DIR)
except Exception as e:                                   # noqa: BLE001 — report, don't die
    SKILLS = {}
    print(f"[skill-router] no catalog loaded from {SKILLS_DIR}: {e}")


mcp = MCPServer(
    name="skill-router",
    title="Skill Router",
    version=SERVER_VERSION,
    instructions=(
        "This server holds PROCEDURES, not capabilities. Before acting on anything beyond "
        "a single lookup, call route_skill(task) (or list_skills) to find the procedure that "
        "fits, then get_skill(name) to load it, then follow it using the tools your other "
        "connected servers provide — the skill names them, this server does not serve them. "
        "If route_skill returns no match, say so and work with the raw tools rather than "
        "forcing the closest procedure onto the task."
    ),
    log_level=os.getenv("MCP_LOG_LEVEL", "INFO"),
)

# Read-only and local: these tools read a directory of Markdown and nothing else.
_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@mcp.tool(annotations=_READ_ONLY)
def list_skills() -> dict[str, Any]:
    """Index of available skills — name, one-line description, and the tools each procedure uses.  Cheap: the whole catalog without any procedure bodies.  Call get_skill(name) for the one you pick."""
    if not SKILLS:
        return {"count": 0, "skills": [], "hint": f"no *.md found in {SKILLS_DIR}"}
    return {
        "count": len(SKILLS),
        "skills": skills_mod.index(SKILLS),
        "next": "call get_skill(name) to load the full procedure before acting",
        "note": "the 'tools' each skill lists are served by your OTHER connected MCP servers, not this one",
    }


@mcp.tool(annotations=_READ_ONLY)
def route_skill(task: str, top_k: int = 3) -> dict[str, Any]:
    """Shortlist the skills worth considering for a task description (lexical BM25-style match over skill metadata — deterministic, no model call, no network).  Returns an empty list when nothing fits, which means: use the raw tools instead of forcing a skill."""
    if not SKILLS:
        return {"task": task, "matches": [], "hint": f"no *.md found in {SKILLS_DIR}"}
    hits = skills_mod.route(task, SKILLS, top_k=max(1, min(top_k, len(SKILLS))))
    return {
        "task": task,
        "matches": [{"name": s.name, "score": round(v, 3), "description": s.description,
                     "tools": s.tools} for s, v in hits],
        "note": "a shortlist, not a decision — empty means no skill fits this task",
    }


@mcp.tool(structured_output=False, annotations=_READ_ONLY)
def get_skill(name: str) -> str:
    """Full procedure for one skill (the Markdown body from <skills>/<name>.md).  Load exactly the one you picked — loading all of them defeats the point of the index."""
    s = SKILLS.get(name)
    if not s:
        return json.dumps({"error": f"no such skill: {name}", "available": sorted(SKILLS)}, indent=2)
    return s.render()


# ---------------------------------------------------------------------------
# Resources and prompt — the same catalog, for clients that prefer either shape
# ---------------------------------------------------------------------------

@mcp.resource("skill://index", mime_type="application/json")
def resource_index() -> str:
    """The skill index — same payload as list_skills()."""
    return json.dumps({"count": len(SKILLS), "skills": skills_mod.index(SKILLS)}, indent=2)


@mcp.resource("skill://{name}", mime_type="text/markdown")
def resource_skill(name: str) -> str:
    """One skill's full procedure by name (skill://triage-alert)."""
    s = SKILLS.get(name)
    return s.render() if s else json.dumps({"error": f"no such skill: {name}",
                                            "available": sorted(SKILLS)}, indent=2)


@mcp.prompt()
def use_skill(task: str) -> str:
    """Route a task to the right skill and run it — the two-stage flow in one prompt."""
    catalog = skills_mod.index_text(SKILLS) if SKILLS else "(no skills installed)"
    return (
        f"Task: {task}\n\nAvailable skills:\n{catalog}\n\n"
        "Pick the ONE skill whose description matches this task, call get_skill(name) to load "
        "its full procedure, then follow that procedure using the tools your other connected "
        "servers provide. If no skill matches, say so and use those tools directly rather than "
        "forcing the closest one. Ground every claim in a tool result."
    )


# ---------------------------------------------------------------------------
# Plain HTTP routes — visible without speaking MCP
# ---------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health_route(request) -> Any:                  # noqa: ARG001
    from starlette.responses import JSONResponse

    return JSONResponse({"ok": True, "service": "skill-router", "skills": len(SKILLS),
                         "now": datetime.now(timezone.utc).isoformat()})


@mcp.custom_route("/info", methods=["GET"])
async def info_route(request) -> Any:                    # noqa: ARG001
    from starlette.responses import JSONResponse

    return JSONResponse({
        "name": mcp.name,
        "version": SERVER_VERSION,
        "skills_dir": str(SKILLS_DIR),
        "tools": [t.name for t in await mcp.list_tools()],
        "skills": skills_mod.index(SKILLS),
        "skill_count": len(SKILLS),
        "client_must_serve": sorted({t for s in SKILLS.values() for t in s.tools}),
        "note": "client_must_serve lists tools the skills reference. This server does NOT "
                "serve them — connect the MCP server(s) that do, or those procedure steps "
                "fail silently.",
    })


# ---------------------------------------------------------------------------
# Auth.  One shared secret in front of the ASGI app — the right size for a
# single-operator deployment.  The MCP spec points multi-tenant remote servers at
# OAuth 2.1 instead; MCPServer takes token_verifier= / auth= for that, and this
# middleware comes out.
# ---------------------------------------------------------------------------

def _bearer_auth_middleware(app):
    """Reject requests without `Authorization: Bearer $MCP_TOKEN`.

    Exempts /health so a load balancer does not need the secret to probe."""
    from starlette.responses import JSONResponse

    async def middleware(scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path", "").rstrip("/") == "/health":
            await app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        # compare_digest: token comparison should not leak length or prefix by timing
        if not hmac.compare_digest(headers.get("authorization", ""), f"Bearer {MCP_TOKEN}"):
            resp = JSONResponse(
                {"error": "unauthorized", "hint": "send Authorization: Bearer <MCP_TOKEN>"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="skill-router"'})
            await resp(scope, receive, send)
            return
        await app(scope, receive, send)

    return middleware


def http_app(*, stateless: bool = False, json_response: bool = False, path: str | None = None):
    """The Streamable HTTP ASGI app, with DNS-rebinding protection and optional auth.

    Built on demand rather than at import: each call creates its own session manager,
    so an importer that only wants the `mcp` object should not pay for a transport.

    `stateless=True` drops per-session state so any replica can serve any request —
    the shape you want behind a load balancer. Every tool here is a single
    request/response, so nothing is lost by it."""
    app = mcp.streamable_http_app(
        streamable_http_path=path or MCP_PATH,
        json_response=json_response,
        stateless_http=stateless,
        transport_security=transport_security(),
        host=MCP_HOST,
    )
    return _bearer_auth_middleware(app) if MCP_TOKEN else app


def sse_app():
    """Legacy HTTP+SSE transport, for clients predating Streamable HTTP."""
    app = mcp.sse_app(transport_security=transport_security(), host=MCP_HOST)
    return _bearer_auth_middleware(app) if MCP_TOKEN else app


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="skill-router — an MCP server for skills")
    ap.add_argument("--host", default=MCP_HOST, help="bind host (env MCP_HOST)")
    ap.add_argument("--port", type=int, default=MCP_PORT, help="bind port (env MCP_PORT)")
    ap.add_argument("--transport", choices=["streamable-http", "sse", "stdio"],
                    default=os.getenv("MCP_TRANSPORT", "streamable-http"),
                    help="stdio when a desktop client spawns the process; streamable-http otherwise")
    ap.add_argument("--path", default=MCP_PATH, help="HTTP path for the MCP endpoint")
    ap.add_argument("--stateless", action="store_true",
                    help="no per-session state (horizontally scalable)")
    ap.add_argument("--json-response", action="store_true",
                    help="plain JSON responses instead of SSE framing")
    ap.add_argument("--check", action="store_true",
                    help="validate the catalog and exit — no listener, for CI")
    args = ap.parse_args()

    if args.check:
        import asyncio

        async def _check() -> int:
            problems = skills_mod.validate(SKILLS)
            needed = sorted({t for s in SKILLS.values() for t in s.tools})
            print(f"skills:    {len(SKILLS)}  ({', '.join(sorted(SKILLS)) or '-'})")
            print(f"tools:     {len(await mcp.list_tools())}")
            print(f"index:     {len(skills_mod.index_text(SKILLS))} bytes in context per request")
            print(f"client must serve: {', '.join(needed) or '-'}")
            for p in problems:
                print(f"PROBLEM: {p}")
            if not SKILLS:
                print(f"PROBLEM: empty catalog at {SKILLS_DIR}")
                return 1
            return 1 if problems else 0

        raise SystemExit(asyncio.run(_check()))

    MCP_HOST, MCP_PORT, MCP_PATH = args.host, args.port, args.path
    if args.transport == "stdio":
        # stdout IS the transport here — anything printed to it corrupts the stream.
        mcp.run(transport="stdio")
    else:
        import uvicorn

        auth = "bearer token REQUIRED" if MCP_TOKEN else "no auth (loopback only — set MCP_TOKEN to publish)"
        print(f"skill-router {SERVER_VERSION} — {len(SKILLS)} skills from {SKILLS_DIR}")
        print(f"  MCP    → http://{args.host}:{args.port}{args.path}  (Streamable HTTP)")
        print(f"  health → http://{args.host}:{args.port}/health")
        print(f"  info   → http://{args.host}:{args.port}/info")
        print(f"  auth: {auth}")
        print(f"  Inspector: npx @modelcontextprotocol/inspector http://{args.host}:{args.port}{args.path}")
        app = (sse_app() if args.transport == "sse"
               else http_app(stateless=args.stateless, json_response=args.json_response,
                             path=args.path))
        uvicorn.run(app, host=args.host, port=args.port,
                    log_level=os.getenv("MCP_LOG_LEVEL", "info").lower())
