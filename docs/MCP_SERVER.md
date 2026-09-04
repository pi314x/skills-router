# Running the server

Two implementations of the same three tools, over the same `skills/*.md`:

| | **Python** `skill_server.py` | **Java** `java-mcp-server/` |
|---|---|---|
| SDK | MCP Python SDK **2.x** | MCP Java SDK 2.0 via Spring AI 2.0 |
| Transports | Streamable HTTP, legacy SSE, stdio | Streamable HTTP |
| Default port | 8001 | 8002 |
| Run it | `python skill_server.py` | Eclipse → Run As, or `mvn spring-boot:run` |

Both declare tool annotations and structured output, and both route a task to the same
skill (the six acceptance cases are pinned in both suites). Java details, including
Eclipse import: [../java-mcp-server/README.md](../java-mcp-server/README.md).

> Needs MCP Python SDK **2.x** — `pip install "mcp<2"` will not run this, because 2.x
> renamed `FastMCP` to `MCPServer` and moved the client entry points.
> `requirements-server.txt` pins it.

---

## 1. Install and run

```bash
pip install -r requirements-server.txt
python skill_server.py                      # http://127.0.0.1:8001/mcp
```

Verify before pointing a client at it:

```bash
python skill_server.py --check              # no listener — safe in CI
curl -s localhost:8001/health
curl -s localhost:8001/info | jq '.skills[].name, .client_must_serve'
```

`--check` exits non-zero on a malformed skill or an empty catalog, and prints the size
of the index in bytes — the thing every request pays for — plus `client must serve`,
the tools your catalog assumes some *other* connected server provides.

Point it at a different catalog with `SKILLS_DIR=/path/to/skills`. That is the
intended way to reuse the server for your own procedures without touching the code.

### Transports

| Flag | Use it when |
|---|---|
| `--transport streamable-http` (default) | Anything networked. POST + SSE on one endpoint |
| `--transport stdio` | A desktop client spawns the process itself |
| `--transport sse` | A client predating Streamable HTTP |
| `--stateless` | Behind a load balancer — any replica serves any request. Nothing is lost: every tool here is one request/response |
| `--json-response` | A proxy that mangles SSE |

On **stdio**, stdout *is* the transport — anything printed to it corrupts the stream.
That is why the banner and `--check` output exist only on the HTTP path.

### Inspector

```bash
npx @modelcontextprotocol/inspector http://localhost:8001/mcp
```

---

## 2. Security

### DNS-rebinding protection (on by default)

A local server on a known port is reachable from any page the user opens, unless it
checks `Host` and `Origin`. This does, and the defaults are loopback-only:

```bash
MCP_ALLOWED_HOSTS=skills.example.com
MCP_ALLOWED_ORIGINS=https://skills.example.com
```

An unlisted `Origin` gets **403**; an unlisted `Host` gets **400**. If a client "can't
connect" after you publish the server, check this first — the rejection is the
protection working.

### Bearer token

Set `MCP_TOKEN` and every request needs `Authorization: Bearer <token>`; `/health`
stays open so a load balancer can probe without the secret.

```bash
MCP_TOKEN="$(openssl rand -hex 32)" python skill_server.py
curl -H "Authorization: Bearer $MCP_TOKEN" localhost:8001/info
```

`llm_router.py` reads the same variable and sends the header. This is one shared
secret compared with `hmac.compare_digest`, which is the right size for a
single-operator deployment. The MCP spec points multi-tenant remote servers at OAuth
2.1 instead (protected-resource metadata, resource indicators); `MCPServer` takes
`token_verifier=` / `auth=` for that, and the middleware comes out.

### TLS

The server speaks plain HTTP. Terminate TLS in a reverse proxy:

```nginx
location /mcp {
    proxy_pass         http://127.0.0.1:8001/mcp;
    proxy_http_version 1.1;
    proxy_set_header   Host $host;
    proxy_set_header   Origin $http_origin;
    # SSE: no buffering, no idle timeout, or streaming responses stall
    proxy_buffering    off;
    proxy_read_timeout 3600s;
}
```

`proxy_buffering off` is not optional. With it on, nginx holds the SSE stream and the
client sees nothing until the response completes.

---

## 3. Deployment

```bash
docker compose up -d --build            # Python on 127.0.0.1:8001
docker compose --profile java up -d     # plus the Java one on 8002
```

The image runs as a non-root user with a read-only root filesystem and mounts the
catalog read-only — the server only ever reads it, and a read-only mount makes that a
property of the deployment rather than a promise in a docstring. The catalog is
mounted rather than baked in, so editing a skill and restarting needs no rebuild.

The port is published to `127.0.0.1` on purpose. Change that only behind the proxy and
auth above.

For systemd, `deploy/skill-router.service` has a hardened unit (`ProtectSystem=strict`,
empty `ReadWritePaths=`, `SystemCallFilter=@system-service`) — it enforces at the
kernel what the tool annotations only claim: this process reads and never writes.
Secrets go in the `EnvironmentFile`, never the unit, which is world-readable.

---

## 4. Connecting clients

### Claude Code

```bash
claude mcp add --transport http skills http://localhost:8001/mcp
claude mcp add --transport http skills http://localhost:8001/mcp \
  --header "Authorization: Bearer $MCP_TOKEN"          # if MCP_TOKEN is set
```

Add your domain servers the same way — the model gets one merged tool list, which is
exactly the composition this server is designed for.

### Claude Desktop

`claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`,
Windows: `%APPDATA%\Claude\`). Desktop spawns the process, so use **stdio** and
absolute paths:

```json
{
  "mcpServers": {
    "skills": {
      "command": "/opt/skill-router/.venv/bin/python",
      "args": ["/opt/skill-router/skill_server.py", "--transport", "stdio"],
      "env": { "PYTHONUNBUFFERED": "1", "SKILLS_DIR": "/opt/skill-router/skills" }
    }
  }
}
```

### The Claude API, without a client at all

```python
client.beta.messages.create(
    betas=["mcp-client-2025-11-20"],
    model="claude-opus-5", max_tokens=16000,
    mcp_servers=[{"type": "url", "url": "https://skills.example.com/mcp", "name": "skills"},
                 {"type": "url", "url": "https://domain.example.com/mcp", "name": "domain"}],
    tools=[{"type": "mcp_toolset", "mcp_server_name": "skills"},
           {"type": "mcp_toolset", "mcp_server_name": "domain"}],
    messages=[{"role": "user", "content": "…"}],
)
```

`mcp_servers` **alone** is a validation error — the matching `mcp_toolset` entry is
the half people forget, one per server. The URLs must be reachable from Anthropic's
side, so `127.0.0.1` will not do; that is what `llm_router.py --mode connector` uses.

### OpenAI

No MCP in the chat loop, so run the client yourself:
`python llm_router.py --provider openai`, which translates the MCP schemas into OpenAI
function tools.

---

## 5. Troubleshooting

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: mcp.server.fastmcp` | `mcp` 1.x installed; this needs 2.x |
| Client gets **403** | `Origin` not in `MCP_ALLOWED_ORIGINS` — the rebinding protection working |
| Client gets **400** | `Host` not in `MCP_ALLOWED_HOSTS` (add the `host:port` clients actually use) |
| Client gets **401** | `MCP_TOKEN` is set; send `Authorization: Bearer …` |
| SSE hangs behind nginx | `proxy_buffering off;` and a long `proxy_read_timeout` |
| stdio client sees garbage | Something printed to stdout; on stdio, stdout is the transport |
| `--check` says empty catalog | `SKILLS_DIR` points somewhere with no `*.md` |
| Model calls a tool that does not exist | It is in `client_must_serve` — connect the server that provides it |
| Java `/info` shows `toolCount: 0` | A bean injected `McpSyncServer` eagerly — see the Java README |
