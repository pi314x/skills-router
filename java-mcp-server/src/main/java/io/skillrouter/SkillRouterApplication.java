package io.skillrouter;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

/**
 * skill-router — an MCP server that serves <em>skills</em>, not domain tools.
 *
 * <p>The same three tools as the Python server ({@code ../skill_server.py}), reading
 * the same {@code skills/*.md} catalog, so a task routes to the same skill on either.
 * This build exists so the server can run as an ordinary Spring Boot app — Run As
 * &gt; Spring Boot App in Eclipse, a breakpoint in {@code SkillRouter.score} — with no
 * Python toolchain in the loop.
 *
 * <p>It composes with your domain MCP servers rather than wrapping them: a skill's
 * {@code tools:} frontmatter names tools the <em>client</em> is expected to have from
 * those other servers, and this process never calls them. {@code GET /info} reports
 * that set as {@code clientMustServe}, because a procedure step naming a tool nobody
 * serves fails silently — the model follows the instruction and invents the call.
 *
 * <p>Tools are registered by annotation scanning: any {@code @McpTool} method on a
 * Spring bean is exposed, so adding a tool is adding a method.
 */
@SpringBootApplication
@EnableConfigurationProperties(SkillsProperties.class)
public class SkillRouterApplication {

    public static void main(String[] args) {
        SpringApplication.run(SkillRouterApplication.class, args);
    }
}
