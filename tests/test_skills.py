"""Offline tests for skills.py — the skill registry and its lexical router.

No network, no LLM, no MCP: the whole point of keeping the router lexical is that
routing quality is testable for free.  The routing cases below are the acceptance
criteria — if a new skill steals one of them, that is a real regression, not a
flaky test."""

import ast
from pathlib import Path

import pytest

import skills as sk

ROOT = Path(__file__).parent.parent
SKILLS_DIR = ROOT / "skills"
SERVER = ROOT / "skill_server.py"


@pytest.fixture(scope="module")
def catalog():
    return sk.load_skills(SKILLS_DIR)


# ----------------------------------------------------------------- loading

def test_ships_skills_and_all_of_them_load(catalog):
    assert len(catalog) >= 5
    for name, s in catalog.items():
        assert s.name == name
        assert s.description.strip()
        assert s.body.strip()


def test_no_validation_problems(catalog):
    assert sk.validate(catalog) == []


def served_tool_names():
    """Tool names in skill_server.py, read with ast rather than a regex.

    The decorators carry arguments (annotations, structured_output) and will carry more
    later; a regex over the source silently matched nothing the first time that changed,
    which turned this test green for the wrong reason."""
    tree = ast.parse(SERVER.read_text())
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            fn = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(fn, ast.Attribute) and fn.attr == "tool":
                kwargs = {k.arg: k.value for k in getattr(dec, "keywords", [])}
                override = kwargs.get("name")
                names.add(override.value if isinstance(override, ast.Constant) else node.name)
    return names


def test_server_exposes_exactly_the_skill_tools(catalog):
    """The server serves procedures, not capabilities.

    If a domain tool ever appears here, the layering has broken: this server would be
    wrapping somebody's tools instead of composing with their server."""
    assert served_tool_names() == {"list_skills", "route_skill", "get_skill"}


def test_referenced_tools_are_documented_not_served(catalog):
    """Skills reference tools that a CLIENT must have from its other MCP servers.

    So `validate` against this server's own tools is expected to complain — that is the
    architecture, not a bug. What must hold is that every skill names at least one tool,
    because a procedure that calls nothing is a note, not a skill."""
    needed = {t for s in catalog.values() for t in s.tools}
    assert needed, "no skill references any tool"
    assert needed.isdisjoint(served_tool_names())
    for s in catalog.values():
        assert s.tools, f"{s.name} references no tools"


def test_frontmatter_requires_name_and_description():
    with pytest.raises(ValueError):
        sk.parse_skill("no frontmatter here")
    with pytest.raises(ValueError):
        sk.parse_skill("---\nname: x\n---\nbody")          # no description
    with pytest.raises(ValueError):
        sk.parse_skill("---\nname: x\ndescription: y\nbody")  # unterminated block


def test_parses_lists_and_body():
    s = sk.parse_skill(
        "---\nname: demo\ndescription: A demo.\nkeywords: alpha, beta\n"
        "tools: get_radar, get_weights\n---\nthe procedure\n"
    )
    assert s.keywords == ["alpha", "beta"]
    assert s.tools == ["get_radar", "get_weights"]
    assert s.body.strip() == "the procedure"


def test_duplicate_names_are_rejected(tmp_path):
    body = "---\nname: dup\ndescription: d\n---\nx\n"
    (tmp_path / "a.md").write_text(body)
    (tmp_path / "b.md").write_text(body)
    with pytest.raises(ValueError, match="duplicate"):
        sk.load_skills(tmp_path)


def test_validate_flags_unknown_tool():
    s = sk.parse_skill("---\nname: d\ndescription: d\ntools: no_such_tool\n---\nx\n")
    problems = sk.validate({"d": s}, known_tools={"get_radar"})
    assert any("no_such_tool" in p for p in problems)


# ------------------------------------------------- progressive disclosure

def test_index_carries_no_bodies(catalog):
    """The index is what sits in context on every request — if bodies leak into it,
    progressive disclosure has quietly stopped happening."""
    blob = sk.index_text(catalog)
    for s in catalog.values():
        assert s.description in blob
        first_body_line = s.body.strip().splitlines()[0]
        assert first_body_line not in blob
    assert len(blob) < 1200          # ~1 line per skill, not one page per skill


def test_render_includes_body_and_tools(catalog):
    s = catalog["triage-alert"]
    out = s.render()
    assert "get_radar" in out and s.description in out
    assert s.body.strip().splitlines()[0] in out


# ------------------------------------------------------------- routing

@pytest.mark.parametrize("task,expected", [
    ("BTSUSDT just alerted at score 6.2 — is it real or noise?",   "triage-alert"),
    ("which low-cap coin is likeliest to get pumped tomorrow?",    "hunt-targets"),
    ("someone posted 'get ready, pump in 5 min on binance'",       "triage-telegram"),
    ("should I buy this with 500 usdt, what size and stop?",       "check-tradeability"),
    ("did we see last night's pump coming, what was the lead?",    "pump-postmortem"),
    ("too many false alerts this week — retrain the weights?",     "tune-detector"),
])
def test_router_picks_the_right_skill(catalog, task, expected):
    hits = sk.route(task, catalog, top_k=3)
    assert hits, f"router returned nothing for {task!r}"
    assert hits[0][0].name == expected, [(s.name, round(v, 2)) for s, v in hits]


@pytest.mark.parametrize("task", [
    "what is the weather in paris",
    "explain the python garbage collector",
    "book me a flight to berlin",
    "summarize this pdf",
])
def test_router_declines_unrelated_tasks(catalog, task):
    """An honest empty result. Forcing the least-bad procedure onto an unrelated
    task is how a router produces confident nonsense."""
    assert sk.route(task, catalog) == []


def test_router_ignores_skill_bodies(catalog):
    """Bodies are prose for the model to follow, not a routing surface — scoring
    them makes every skill a weak match for any word its instructions happen to
    use, which is how 'explain the python garbage collector' once matched
    check-tradeability."""
    assert "body" not in sk._FIELD_WEIGHTS
    s = sk.parse_skill("---\nname: narrow\ndescription: Only about widgets.\n---\n"
                       "This body mentions kangaroos at length. kangaroos kangaroos.\n")
    assert sk.route("kangaroos", {"narrow": s}) == []


def test_router_is_deterministic_and_ordered(catalog):
    task = "radar alert triage"
    a = [(s.name, round(v, 6)) for s, v in sk.route(task, catalog, top_k=6)]
    b = [(s.name, round(v, 6)) for s, v in sk.route(task, catalog, top_k=6)]
    assert a == b
    assert [v for _, v in a] == sorted((v for _, v in a), reverse=True)


def test_top_k_bounds_the_shortlist(catalog):
    assert len(sk.route("pump radar alert coin score", catalog, top_k=2)) <= 2


def test_empty_query_matches_nothing(catalog):
    assert sk.route("", catalog) == []
    assert sk.route("the and of to", catalog) == []      # stopwords only
