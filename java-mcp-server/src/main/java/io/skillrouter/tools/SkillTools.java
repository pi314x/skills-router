package io.skillrouter.tools;

import java.util.List;
import java.util.stream.Collectors;

import org.springframework.ai.mcp.annotation.McpArg;
import org.springframework.ai.mcp.annotation.McpPrompt;
import org.springframework.ai.mcp.annotation.McpResource;
import org.springframework.ai.mcp.annotation.McpTool;
import org.springframework.ai.mcp.annotation.McpToolParam;
import org.springframework.stereotype.Service;

import io.modelcontextprotocol.spec.McpSchema;
import io.skillrouter.skills.Skill;
import io.skillrouter.skills.SkillRegistry;
import io.skillrouter.skills.SkillRouter;

/**
 * The skill-routing tools — progressive disclosure in three methods.
 *
 * <p>{@code list_skills} and {@code route_skill} are cheap and return only metadata;
 * {@code get_skill} returns the full procedure for the one that was chosen. That split
 * is the whole design: the catalog costs one line per skill in context instead of a
 * page, so it can grow without every request paying for it.
 *
 * <p>Every tool here is read-only and touches nothing but local files, hence
 * {@code readOnlyHint = true, openWorldHint = false} on all three. Clients use those
 * hints to decide what may run without asking the user.
 */
@Service
public class SkillTools {

    private final SkillRegistry registry;
    private final SkillRouter router;

    public SkillTools(SkillRegistry registry, SkillRouter router) {
        this.registry = registry;
        this.router = router;
    }

    public record SkillIndex(int count, List<Skill.IndexEntry> skills, String next) {
    }

    public record RouteResult(String task, List<SkillRouter.Match> matches, String note) {
    }

    @McpTool(name = "list_skills", generateOutputSchema = true,
            description = "Index of available skills — name + one-line description + the tools each "
                    + "procedure uses. Cheap: the whole catalog without any procedure bodies. "
                    + "Call get_skill(name) for the one you pick. The listed tools are served by "
                    + "your OTHER connected MCP servers, not this one.",
            annotations = @McpTool.McpAnnotations(readOnlyHint = true, openWorldHint = false))
    public SkillIndex listSkills() {
        List<Skill.IndexEntry> entries = registry.all().stream().map(Skill::indexEntry).toList();
        return new SkillIndex(entries.size(), entries,
                "call get_skill(name) to load the full procedure before acting");
    }

    @McpTool(name = "route_skill", generateOutputSchema = true,
            description = "Shortlist the skills worth considering for a task description "
                    + "(lexical BM25-style match — deterministic, no model call, no network). "
                    + "Returns an empty list when nothing fits, which means: use the raw tools "
                    + "instead of forcing a skill.",
            annotations = @McpTool.McpAnnotations(readOnlyHint = true, openWorldHint = false))
    public RouteResult routeSkill(
            @McpToolParam(description = "The task, in plain language", required = true) String task,
            @McpToolParam(description = "Shortlist size (default 3)", required = false) Integer topK) {
        int k = topK == null ? 3 : Math.max(1, topK);
        List<SkillRouter.Match> matches = router.route(task, registry.all(), k);
        return new RouteResult(task, matches,
                "a shortlist, not a decision — empty means no skill fits this task");
    }

    @McpTool(name = "get_skill",
            description = "Full procedure for one skill (the Markdown body from skills/<name>.md). "
                    + "Load exactly the one you picked — loading all of them defeats the point.",
            annotations = @McpTool.McpAnnotations(readOnlyHint = true, openWorldHint = false))
    public String getSkill(
            @McpToolParam(description = "Skill name, e.g. triage-alert", required = true) String name) {
        return registry.get(name).map(Skill::render).orElseGet(
                () -> "no such skill: " + name + "\navailable: " + String.join(", ", registry.names()));
    }

    @McpResource(uri = "toolkit://skills", name = "skills",
            description = "The skill index — same payload as list_skills(), for clients that prefer resources.",
            mimeType = "application/json")
    public String skillsResource() {
        return registry.all().stream()
                .map(s -> "- " + s.name() + ": " + s.description())
                .collect(Collectors.joining("\n"));
    }

    @McpResource(uri = "skill://{name}", name = "skill",
            description = "One skill's full procedure by name (skill://triage-alert).",
            mimeType = "text/markdown")
    public String skillResource(String name) {
        return registry.get(name).map(Skill::render)
                .orElse("no such skill: " + name);
    }

    @McpPrompt(name = "use_skill",
            description = "Route a task to the right skill and run it — the two-stage flow in one prompt.")
    public McpSchema.GetPromptResult useSkill(
            @McpArg(name = "task", description = "What you want done", required = true) String task) {
        String catalog = registry.all().isEmpty() ? "(no skills installed)"
                : registry.all().stream().map(s -> "- " + s.name() + ": " + s.description())
                        .collect(Collectors.joining("\n"));
        String text = """
                Task: %s

                Available skills:
                %s

                Pick the ONE skill whose description matches this task, call get_skill(name) to load \
                its full procedure, then follow that procedure using the toolkit's tools. If no skill \
                matches, say so and use the raw tools directly rather than forcing the closest one. \
                Ground every number in a tool result — never invent a symbol or a statistic.\
                """.formatted(task, catalog);
        return new McpSchema.GetPromptResult("Route a task to the right skill",
                List.of(new McpSchema.PromptMessage(McpSchema.Role.USER, new McpSchema.TextContent(text))));
    }

    /** Every tool the loaded skills reference — what a client must serve for them to run. */
    public List<String> clientMustServe() {
        return registry.all().stream().flatMap(s -> s.tools().stream())
                .distinct().sorted().toList();
    }
}
