package io.skillrouter.skills;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.springframework.stereotype.Component;

/**
 * The lexical pre-filter: a BM25-ish score of a task string against skill metadata.
 *
 * <p>Deliberately dependency-free and deterministic — no embeddings, no model call, no
 * network — so it is cheap enough to run on every request and testable offline. It is a
 * shortlist, not a decision: the model still chooses.
 *
 * <p>Two properties are load-bearing and both have tests:
 *
 * <ul>
 * <li><b>It scores metadata only, never bodies.</b> A body is prose for the model to
 * follow; scoring it makes every skill a weak match for any word its instructions
 * happen to use ("explain the python garbage collector" once matched the skill whose
 * procedure says "explain"). So {@code description} is the entire routing surface —
 * worth more editing time than the procedure is.
 * <li><b>It can return nothing.</b> An empty result means "no skill fits, use the raw
 * tools". A router that always returns its best guess will confidently apply a trading
 * procedure to a question about the weather, and a procedure reads like authority.
 * </ul>
 *
 * <p>Kept behaviourally identical to {@code ../skills.py} so both servers route the
 * same way; {@code SkillRouterTest} pins the same six acceptance cases.
 */
@Component
public class SkillRouter {

    public static final double DEFAULT_MIN_SCORE = 0.5;

    private static final Pattern TOKEN = Pattern.compile("[a-z0-9_]+");

    /** Words that appear in nearly every task string here and so separate nothing. */
    private static final Set<String> STOPWORDS = Set.of(
            "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by",
            "is", "are", "was", "were", "be", "been", "it", "its", "this", "that",
            "i", "we", "you", "my", "our", "me", "please", "can", "could", "should", "would",
            "do", "does", "did", "how", "what", "why", "when", "which");

    public record Match(String name, double score, String description, List<String> tools) {
    }

    /** Lowercase word tokens, stopworded, crudely singularized (flags -> flag). */
    static List<String> tokenize(String text) {
        List<String> out = new ArrayList<>();
        Matcher m = TOKEN.matcher(text.toLowerCase());
        while (m.find()) {
            String w = m.group();
            if (w.length() < 2 || STOPWORDS.contains(w)) {
                continue;
            }
            if (w.length() > 3 && w.endsWith("s") && !w.endsWith("ss")) {
                w = w.substring(0, w.length() - 1);
            }
            out.add(w);
        }
        return out;
    }

    /**
     * Inverse document frequency over the catalog. With six skills this mostly does one
     * job: kill the words every skill shares ("pump", "radar", "coin") so they stop
     * dominating every score.
     */
    static Map<String, Double> idf(Collection<Skill> skills) {
        int n = skills.size();
        Map<String, Integer> df = new HashMap<>();
        for (Skill s : skills) {
            Set<String> seen = new HashSet<>();
            for (Skill.Field f : Skill.Field.values()) {
                seen.addAll(tokenize(s.field(f)));
            }
            for (String t : seen) {
                df.merge(t, 1, Integer::sum);
            }
        }
        Map<String, Double> out = new HashMap<>();
        df.forEach((t, c) -> out.put(t, Math.log(1.0 + (n - c + 0.5) / (c + 0.5))));
        return out;
    }

    /**
     * Score one skill's metadata against a task string. Term frequency is saturated
     * ({@code tf/(tf+1)}) so padding a keyword list with the same word nine times
     * cannot outrank a skill actually named after it.
     */
    static double score(String query, Skill skill, Map<String, Double> idf) {
        Set<String> q = new HashSet<>(tokenize(query));
        if (q.isEmpty()) {
            return 0.0;
        }
        double total = 0.0;
        for (Skill.Field field : Skill.Field.values()) {
            List<String> toks = tokenize(skill.field(field));
            if (toks.isEmpty()) {
                continue;
            }
            Map<String, Integer> counts = new HashMap<>();
            for (String t : toks) {
                counts.merge(t, 1, Integer::sum);
            }
            for (String t : q) {
                int tf = counts.getOrDefault(t, 0);
                if (tf > 0) {
                    total += field.weight * idf.getOrDefault(t, 1.0) * (tf / (tf + 1.0));
                }
            }
        }
        return total;
    }

    public List<Match> route(String query, Collection<Skill> skills, int topK) {
        return route(query, skills, topK, DEFAULT_MIN_SCORE);
    }

    /**
     * Shortlist the skills worth showing the model, best first.
     *
     * @return an empty list when nothing clears {@code minScore} — an honest "no skill
     *         fits", which the caller must treat as "hand the model the raw tools",
     *         never as "use the least-bad skill"
     */
    public List<Match> route(String query, Collection<Skill> skills, int topK, double minScore) {
        if (skills.isEmpty()) {
            return List.of();
        }
        List<Skill> ranked = skills.stream().sorted(Comparator.comparing(Skill::name)).toList();
        Map<String, Double> idf = idf(ranked);
        return ranked.stream()
                .map(s -> new Match(s.name(), round(score(query, s, idf)), s.description(), s.tools()))
                .filter(m -> m.score() >= minScore)
                .sorted(Comparator.comparingDouble(Match::score).reversed().thenComparing(Match::name))
                .limit(Math.max(1, topK))
                .toList();
    }

    private static double round(double v) {
        return Math.round(v * 1000.0) / 1000.0;
    }
}
