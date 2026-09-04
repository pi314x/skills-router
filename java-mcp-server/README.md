# Java build (Spring Boot) — run it in Eclipse

The same three tools as `../skill_server.py`, over the same `../skills/*.md`, as an
ordinary Spring Boot app: **Run As → Spring Boot App**, breakpoints in the routing
code, no Python toolchain in the loop.

`SkillRouter` is a port of `../skills.py`, and `SkillRouterTest` pins the same six
routing acceptance cases — both implementations score an identical task identically.
If they ever diverge, one of them is quietly giving different advice.

| | Python (`../skill_server.py`) | Java (this module) |
|---|---|---|
| SDK | MCP Python SDK 2.x | MCP Java SDK 2.0 via Spring AI 2.0 |
| Transport | Streamable HTTP + legacy SSE + stdio | Streamable HTTP |
| Port | 8001 | 8002 |
| Tools | `list_skills`, `route_skill`, `get_skill` | the same three |

## Eclipse

Needs a JDK 21+ and the Eclipse **Java** + **Maven (m2e)** tooling that ships with
Eclipse IDE for Enterprise Java and Web Developers. Spring Tools 4 is optional and
only adds the nicer *Spring Boot App* launcher.

1. **File → Import… → Maven → Existing Maven Projects**
2. Root directory: this `java-mcp-server/` folder → **Finish**
   (first import downloads Spring Boot 4.1 and Spring AI 2.0 — a few minutes)
3. Open `SkillRouterApplication.java` → **Run As → Java Application**
   (or *Spring Boot App* with Spring Tools 4 installed)
4. It starts on <http://127.0.0.1:8002>, reading the catalog from `../skills`

If Eclipse shows `Project configuration is not up-to-date`, right-click the project →
**Maven → Update Project…** (`Alt+F5`) and tick *Force Update of Snapshots/Releases*.

**Serving a different catalog.** *Run Configurations → Arguments → Program arguments*:

```
--skills.dir=/path/to/your/skills --server.port=8002
```

**Where to put a breakpoint.** `SkillRouter.score` is the interesting one — it is the
entire routing decision, it is pure, and it needs no live data to step through.
`SkillRegistry.parse` is the other, for a frontmatter block that will not load.

## Command line

```bash
mvn spring-boot:run                                   # dev
mvn test                                              # 18 offline tests
mvn package && java -jar target/skill-router-mcp-server-1.0.0.jar

curl localhost:8002/actuator/health                   # liveness/readiness
curl localhost:8002/info | jq '.skillCount, .clientMustServe'
npx @modelcontextprotocol/inspector http://localhost:8002/mcp
```

`clientMustServe` is the union of every tool the loaded skills reference. This server
serves none of them — a client is expected to have them from its other MCP servers —
and a procedure step naming a tool nobody serves fails **silently**, because the model
follows the instruction and invents the call. Better on a dashboard than in an answer.

## Adding a tool

Add a method to a Spring bean; annotation scanning does the rest
(`spring.ai.mcp.server.annotation-scanner.enabled`, on by default):

```java
@McpTool(name = "get_thing", generateOutputSchema = true,
        description = "One clear sentence — this is what the model routes on.",
        annotations = @McpTool.McpAnnotations(readOnlyHint = true, openWorldHint = false))
public Thing getThing(@McpToolParam(description = "…", required = true) String id) { … }
```

Return a **record**, not a JSON string: `generateOutputSchema = true` derives the
tool's `outputSchema` from it, so the client gets `structuredContent` as well as text.
Set `openWorldHint = true` if the method reaches an external system — clients use
these hints to decide what may run without asking the user.

## One gotcha worth knowing

**Never inject `McpSyncServer` eagerly.** Tools are collected by a
`BeanPostProcessor` as the annotated beans are created, so a bean that depends on
`McpSyncServer` directly can force the server to be built *before* the tool beans have
been post-processed — and it starts with **zero tools**, silently, with a healthy log.
`InfoController` takes it as `@Lazy` for exactly this reason. If `/info` ever reports
`toolCount: 0`, this is why.
