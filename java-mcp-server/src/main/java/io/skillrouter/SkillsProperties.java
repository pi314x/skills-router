package io.skillrouter;

import java.nio.file.Path;
import java.nio.file.Paths;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Where the skill catalog lives.
 *
 * <p>Defaults to {@code ../skills}, which is correct when this module sits inside the
 * project checkout as {@code java-mcp-server/}. Point {@code skills.dir} (or
 * {@code SKILLS_DIR}) elsewhere to serve a different catalog — that is the intended
 * way to reuse this server for your own procedures without touching the code.
 */
@ConfigurationProperties(prefix = "skills")
public class SkillsProperties {

    private String dir = "../skills";

    public String getDir() {
        return dir;
    }

    public void setDir(String dir) {
        this.dir = dir;
    }

    public Path dirPath() {
        return Paths.get(dir).toAbsolutePath().normalize();
    }
}
