package io.skillrouter;

import java.util.List;
import java.util.Map;
import java.util.TreeSet;

import org.springframework.context.annotation.Lazy;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import io.modelcontextprotocol.server.McpSyncServer;
import io.skillrouter.skills.Skill;
import io.skillrouter.skills.SkillRegistry;

/**
 * Plain-JSON {@code /info} — the same shape the Python server serves, so a load
 * balancer, a dashboard or a human with curl can see what this process holds without
 * speaking MCP. Liveness and readiness come from Actuator at
 * {@code /actuator/health}; this endpoint is about the catalog, not uptime.
 *
 * <p>{@code clientMustServe} is the field worth watching: the union of every tool the
 * loaded skills reference. This server does not serve any of them — a client is
 * expected to have them from its other MCP servers — and a procedure step naming a
 * tool nobody serves fails <em>silently</em>, because the model follows the
 * instruction and invents the call. Better on a dashboard than in an answer.
 *
 * <p><b>Never inject {@link McpSyncServer} eagerly.</b> Tools are collected by a
 * {@code BeanPostProcessor} as the annotated beans are created, so a bean depending on
 * it directly can force the server to be built <em>before</em> the tool beans have
 * been post-processed — and it starts with zero tools, silently, with a healthy log.
 * Hence {@code @Lazy}. If {@code toolCount} is ever 0, this is why.
 */
@RestController
public class InfoController {

    private final McpSyncServer server;
    private final SkillRegistry registry;
    private final SkillsProperties props;

    public InfoController(@Lazy McpSyncServer server, SkillRegistry registry,
                          SkillsProperties props) {
        this.server = server;
        this.registry = registry;
        this.props = props;
    }

    @GetMapping("/info")
    public Map<String, Object> info() {
        List<String> tools = server.listTools().stream()
                .map(io.modelcontextprotocol.spec.McpSchema.Tool::name).sorted().toList();
        List<String> needed = registry.all().stream()
                .flatMap(s -> s.tools().stream())
                .collect(java.util.stream.Collectors.toCollection(TreeSet::new))
                .stream().toList();
        return Map.of(
                "name", server.getServerInfo().name(),
                "version", server.getServerInfo().version(),
                "implementation", "java",
                "skillsDir", props.dirPath().toString(),
                "tools", tools,
                "toolCount", tools.size(),
                "skills", registry.all().stream().map(Skill::indexEntry).toList(),
                "skillCount", registry.all().size(),
                "clientMustServe", needed,
                "note", "clientMustServe lists tools the skills reference. This server does NOT "
                        + "serve them — connect the MCP server(s) that do, or those procedure "
                        + "steps fail silently.");
    }
}
