package io.skillrouter.skills;

import java.util.List;

/**
 * One procedure: frontmatter metadata plus the Markdown body the model reads.
 *
 * <p>The split matters. {@link #indexEntry()} is what sits in the model's context on
 * every request; {@link #render()} is fetched only once a skill has been chosen. Put
 * the body in the index and progressive disclosure has quietly stopped happening.
 */
public record Skill(String name, String description, List<String> keywords,
                    List<String> tools, String body) {

    /** The cheap half — always in context. No body: that is the whole point. */
    public IndexEntry indexEntry() {
        return new IndexEntry(name, description, tools);
    }

    /** The expensive half — the full procedure, fetched by name. */
    public String render() {
        StringBuilder sb = new StringBuilder();
        sb.append("# Skill: ").append(name).append("\n\n").append(description).append('\n');
        if (!tools.isEmpty()) {
            sb.append("\nTools this skill uses: ").append(String.join(", ", tools)).append('\n');
        }
        return sb.append('\n').append(body.strip()).append('\n').toString();
    }

    /** The field the router scores. Never the body — see {@link SkillRouter}. */
    String field(Field which) {
        return switch (which) {
            case NAME -> name.replace('-', ' ');
            case KEYWORDS -> String.join(" ", keywords);
            case DESCRIPTION -> description;
        };
    }

    public record IndexEntry(String name, String description, List<String> tools) {
    }

    enum Field {
        NAME(4.0), KEYWORDS(3.0), DESCRIPTION(2.0);

        final double weight;

        Field(double weight) {
            this.weight = weight;
        }
    }
}
