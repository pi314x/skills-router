package io.skillrouter.skills;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Stream;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import io.skillrouter.SkillsProperties;

/**
 * Loads {@code skills/*.md} — the same files the Python server reads.
 *
 * <p>Frontmatter is parsed by hand rather than with a YAML library: the format is four
 * scalar keys, and a dependency for that is a dependency to keep in sync with the
 * Python side for no gain.
 *
 * <p>The catalog is loaded once at startup and cached. Skills change when a human edits
 * a file, not per request, and re-reading six files on every {@code list_skills} call
 * would be work done for nobody. {@link #reload()} exists for the tests.
 */
@Component
public class SkillRegistry {

    private static final Logger log = LoggerFactory.getLogger(SkillRegistry.class);

    private final SkillsProperties props;
    private volatile Map<String, Skill> skills = Map.of();

    public SkillRegistry(SkillsProperties props) {
        this.props = props;
        reload();
    }

    public final void reload() {
        Path dir = props.dirPath();
        if (!Files.isDirectory(dir)) {
            log.warn("no skills directory at {} — the skill tools will report an empty catalog", dir);
            this.skills = Map.of();
            return;
        }
        Map<String, Skill> loaded = new LinkedHashMap<>();
        try (Stream<Path> files = Files.list(dir)) {
            List<Path> md = files.filter(p -> p.getFileName().toString().endsWith(".md")).sorted().toList();
            for (Path p : md) {
                Skill s = parse(Files.readString(p, StandardCharsets.UTF_8), p.toString());
                if (loaded.putIfAbsent(s.name(), s) != null) {
                    throw new IllegalStateException(p + ": duplicate skill name '" + s.name() + "'");
                }
            }
        } catch (IOException e) {
            throw new UncheckedIOException("failed to read skills from " + dir, e);
        }
        this.skills = Map.copyOf(loaded);
        log.info("loaded {} skills from {}", loaded.size(), dir);
    }

    public Collection<Skill> all() {
        return skills.values().stream().sorted((a, b) -> a.name().compareTo(b.name())).toList();
    }

    public Optional<Skill> get(String name) {
        return Optional.ofNullable(skills.get(name));
    }

    public List<String> names() {
        return all().stream().map(Skill::name).toList();
    }

    /**
     * Parse one {@code ---} frontmatter block plus Markdown body.
     *
     * <p>Throws rather than skipping a malformed file: a skill that fails to load is a
     * routing hole, and a hole nobody can see is the expensive kind.
     */
    static Skill parse(String text, String source) {
        String s = text.stripLeading();
        if (!s.startsWith("---")) {
            throw new IllegalArgumentException(source + ": missing '---' frontmatter block");
        }
        String rest = s.substring(3).stripLeading();
        int end = rest.indexOf("\n---");
        if (end < 0) {
            throw new IllegalArgumentException(source + ": unterminated frontmatter block");
        }
        String meta = rest.substring(0, end);
        String body = rest.substring(end + 4).stripLeading();

        Map<String, String> kv = new LinkedHashMap<>();
        for (String raw : meta.split("\\R")) {
            String line = raw.strip();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            int colon = line.indexOf(':');
            if (colon < 0) {
                throw new IllegalArgumentException(source + ": bad frontmatter line '" + line + "'");
            }
            kv.put(line.substring(0, colon).strip().toLowerCase(),
                   unquote(line.substring(colon + 1).strip()));
        }
        for (String required : List.of("name", "description")) {
            if (kv.getOrDefault(required, "").isBlank()) {
                throw new IllegalArgumentException(source + ": frontmatter needs a " + required);
            }
        }
        return new Skill(kv.get("name"), kv.get("description"),
                         splitList(kv.get("keywords")), splitList(kv.get("tools")), body);
    }

    private static String unquote(String v) {
        if (v.length() >= 2 && ((v.startsWith("\"") && v.endsWith("\""))
                || (v.startsWith("'") && v.endsWith("'")))) {
            return v.substring(1, v.length() - 1);
        }
        return v;
    }

    private static List<String> splitList(String v) {
        if (v == null || v.isBlank()) {
            return List.of();
        }
        List<String> out = new ArrayList<>();
        for (String part : v.split(",")) {
            String p = part.strip();
            if (!p.isEmpty()) {
                out.add(p);
            }
        }
        return List.copyOf(out);
    }
}
