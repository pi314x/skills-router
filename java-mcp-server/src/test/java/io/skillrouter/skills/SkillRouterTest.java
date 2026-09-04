package io.skillrouter.skills;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

import io.skillrouter.SkillsProperties;

/**
 * Offline tests for the skill catalog and its router — no Spring context, no network.
 *
 * <p>The six routing cases are acceptance criteria and mirror {@code ../tests/test_skills.py}
 * exactly: both implementations must route the same task to the same skill, or one of them
 * is quietly giving different advice. They are written against the example catalog that
 * ships here; swap in your own skills and these are the tests to rewrite first, because a
 * router with no pinned cases is a router nobody notices breaking.
 */
class SkillRouterTest {

    private static SkillRegistry registry;
    private static SkillRouter router;

    @BeforeAll
    static void load() {
        SkillsProperties props = new SkillsProperties();
        props.setDir("../skills");                  // the catalog in the project checkout
        registry = new SkillRegistry(props);
        router = new SkillRouter();
    }

    @Test
    void loadsTheShippedCatalog() {
        assertThat(registry.all()).hasSizeGreaterThanOrEqualTo(5);
        assertThat(registry.all()).allSatisfy(s -> {
            assertThat(s.name()).isNotBlank();
            assertThat(s.description()).isNotBlank();
            assertThat(s.body()).isNotBlank();
        });
    }

    @ParameterizedTest
    @CsvSource(delimiter = '|', value = {
        "BTSUSDT just alerted at score 6.2 - is it real or noise?   | triage-alert",
        "which low-cap coin is likeliest to get pumped tomorrow?    | hunt-targets",
        "someone posted 'get ready, pump in 5 min on binance'       | triage-telegram",
        "should I buy this with 500 usdt, what size and stop?       | check-tradeability",
        "did we see last night's pump coming, what was the lead?    | pump-postmortem",
        "too many false alerts this week - retrain the weights?     | tune-detector",
    })
    void routesTheTaskToTheRightSkill(String task, String expected) {
        List<SkillRouter.Match> hits = router.route(task, registry.all(), 3);
        assertThat(hits).as("router returned nothing for '%s'", task).isNotEmpty();
        assertThat(hits.get(0).name()).as("shortlist was %s", hits).isEqualTo(expected);
    }

    @ParameterizedTest
    @ValueSource(strings = {
        "what is the weather in paris",
        "explain the python garbage collector",
        "book me a flight to berlin",
        "summarize this pdf",
    })
    void declinesUnrelatedTasks(String task) {
        // An honest empty result. Forcing the least-bad procedure onto an unrelated task
        // is how a router produces confident nonsense.
        assertThat(router.route(task, registry.all(), 3)).isEmpty();
    }

    @Test
    void ignoresSkillBodies() {
        // Bodies are prose for the model to follow, not a routing surface.
        Skill narrow = SkillRegistry.parse("""
                ---
                name: narrow
                description: Only about widgets.
                ---
                This body mentions kangaroos at length. kangaroos kangaroos.
                """, "<test>");
        assertThat(router.route("kangaroos", List.of(narrow), 3)).isEmpty();
    }

    @Test
    void isDeterministicAndOrdered() {
        List<SkillRouter.Match> a = router.route("radar alert triage", registry.all(), 6);
        List<SkillRouter.Match> b = router.route("radar alert triage", registry.all(), 6);
        assertThat(a).isEqualTo(b);
        assertThat(a).isSortedAccordingTo((x, y) -> Double.compare(y.score(), x.score()));
    }

    @Test
    void boundsTheShortlist() {
        assertThat(router.route("pump radar alert coin score", registry.all(), 2)).hasSizeLessThanOrEqualTo(2);
    }

    @Test
    void emptyAndStopwordOnlyQueriesMatchNothing() {
        assertThat(router.route("", registry.all(), 3)).isEmpty();
        assertThat(router.route("the and of to", registry.all(), 3)).isEmpty();
    }

    @Test
    void indexCarriesNoBodies() {
        // The index sits in context on every request — bodies leaking into it means
        // progressive disclosure has quietly stopped happening.
        String blob = registry.all().stream()
                .map(s -> "- " + s.name() + ": " + s.description())
                .reduce("", (x, y) -> x + "\n" + y);
        assertThat(blob.length()).isLessThan(1200);
        assertThat(registry.all()).allSatisfy(s -> {
            assertThat(blob).contains(s.description());
            assertThat(blob).doesNotContain(s.body().strip().lines().findFirst().orElseThrow());
        });
    }

    @Test
    void frontmatterIsValidated() {
        assertThatThrownBy(() -> SkillRegistry.parse("no frontmatter here", "<t>"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> SkillRegistry.parse("---\nname: x\n---\nbody", "<t>"))
                .isInstanceOf(IllegalArgumentException.class);      // no description
        assertThatThrownBy(() -> SkillRegistry.parse("---\nname: x\ndescription: y\nbody", "<t>"))
                .isInstanceOf(IllegalArgumentException.class);      // unterminated block
    }

    @Test
    void parsesListsAndBody() {
        Skill s = SkillRegistry.parse("""
                ---
                name: demo
                description: A demo.
                keywords: alpha, beta
                tools: get_radar, get_weights
                ---
                the procedure
                """, "<t>");
        assertThat(s.keywords()).containsExactly("alpha", "beta");
        assertThat(s.tools()).containsExactly("get_radar", "get_weights");
        assertThat(s.body().strip()).isEqualTo("the procedure");
        assertThat(s.render()).contains("get_radar").contains("the procedure");
    }
}
