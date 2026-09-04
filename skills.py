"""
skills.py — the skill registry + lexical router.

A **tool** is one capability with a JSON schema (`get_radar`, `create_ticket`), served
by whichever MCP server owns that domain.  A **skill** is a *procedure*: which of
those tools to call, in what order, and how to read the result without fooling
yourself.  Skills live in `skills/*.md` as plain Markdown with a small frontmatter
block — data, not code, so adding one never touches Python.

Why a registry at all?  Because "let the LLM pick" only scales while every option fits
in the prompt.  A dozen tools fit; a dozen tools x a dozen procedures, each a page of
instructions, do not.  So the registry does **progressive disclosure**:

    1. always in context:  the INDEX — one line per skill (name + description)
    2. loaded on demand:   the BODY — the full procedure, fetched by name

`route()` adds an optional pre-filter in front of that: a BM25-ish lexical score over
the catalog that shortlists the top-k skills for a task string.  It is deliberately
dependency-free and deterministic — no embeddings, no model call, no network — so it
is cheap enough to run on every request and testable offline.  Use it to shrink the
index, or to skip a model round trip entirely on an obvious match; the model still
makes the final call.

Everything here is stdlib-only and knows nothing about MCP, OpenAI or Anthropic —
`skill_server.py` serves it over MCP, `llm_router.py` drives it from either provider,
and `java-mcp-server/` is the same registry in Java.

    python skills.py --list                       # the index the model sees
    python skills.py --route "a coin alerted at score 6.2"
    python skills.py --show triage-alert          # the full procedure
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SKILLS_DIR = Path(__file__).parent / "skills"

# Frontmatter keys that are comma-separated lists rather than scalars.
_LIST_KEYS = ("keywords", "tools")

# Field weights for the lexical router.  The body is deliberately absent: it is prose
# written for the model to *follow*, not to be matched against, and scoring it makes
# every skill a weak match for everything (the body of check-tradeability contains the
# word "explain", so "explain the python garbage collector" used to match it).  Routing
# happens on the metadata alone, which is why the description is the field worth
# spending time on — it is the entire routing surface.
_FIELD_WEIGHTS = {"name": 4.0, "keywords": 3.0, "description": 2.0}

# Words that appear in nearly every task string here and so separate nothing.
_STOPWORDS = frozenset("""
a an the and or of to in on for with at by is are was were be been it its this that
i we you my our me please can could should would do does did how what why when which
""".split())


def _tokenize(text: str) -> List[str]:
    """Lowercase word tokens, stopworded, crudely singularized (flags→flag)."""
    out = []
    for w in re.findall(r"[a-z0-9_]+", text.lower()):
        if w in _STOPWORDS or len(w) < 2:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


@dataclass
class Skill:
    """One procedure: frontmatter metadata + the Markdown body the model reads."""

    name: str
    description: str
    keywords: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    body: str = ""
    path: Optional[Path] = None

    # -- what the model sees, in two sizes ---------------------------------

    def index_entry(self) -> Dict[str, object]:
        """The cheap half — always in context.  No body: that is the whole point."""
        return {"name": self.name, "description": self.description, "tools": self.tools}

    def render(self) -> str:
        """The expensive half — fetched by name once the model has chosen."""
        head = f"# Skill: {self.name}\n\n{self.description}\n"
        if self.tools:
            head += f"\nTools this skill uses: {', '.join(self.tools)}\n"
        return f"{head}\n{self.body.strip()}\n"

    def _field(self, which: str) -> str:
        if which == "name":
            return self.name.replace("-", " ")
        if which == "keywords":
            return " ".join(self.keywords)
        if which == "description":
            return self.description
        return self.body


# ---------------------------------------------------------------------------
# Loading — frontmatter is parsed by hand so the toolkit gains no YAML dependency
# ---------------------------------------------------------------------------

def parse_skill(text: str, path: Optional[Path] = None) -> Skill:
    """Parse one `---` frontmatter block + Markdown body into a Skill.

    Raises ValueError on a missing block or a missing name/description — a skill
    the router cannot describe is worse than no skill, because it still costs a
    line of context on every single request."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        raise ValueError(f"{path or '<string>'}: missing '---' frontmatter block")
    rest = stripped[3:].lstrip("\r\n")
    end = rest.find("\n---")
    if end < 0:
        raise ValueError(f"{path or '<string>'}: unterminated frontmatter block")
    meta_src, body = rest[:end], rest[end + 4:].lstrip("\r\n")

    meta: Dict[str, str] = {}
    for line in meta_src.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path or '<string>'}: bad frontmatter line {line!r}")
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip().strip('"').strip("'")

    for required in ("name", "description"):
        if not meta.get(required):
            raise ValueError(f"{path or '<string>'}: frontmatter needs a {required}")

    lists = {k: [p.strip() for p in meta.get(k, "").split(",") if p.strip()] for k in _LIST_KEYS}
    return Skill(
        name=meta["name"],
        description=meta["description"],
        keywords=lists["keywords"],
        tools=lists["tools"],
        body=body,
        path=path,
    )


def load_skills(root: Path | str = SKILLS_DIR) -> Dict[str, Skill]:
    """Load every `skills/*.md` into {name: Skill}, sorted by name.

    A malformed file raises rather than being skipped silently: a skill that fails
    to load is a routing hole, and a hole you cannot see is the expensive kind."""
    root = Path(root)
    skills: Dict[str, Skill] = {}
    for p in sorted(root.glob("*.md")):
        s = parse_skill(p.read_text(encoding="utf-8"), p)
        if s.name in skills:
            raise ValueError(f"{p}: duplicate skill name {s.name!r} (also {skills[s.name].path})")
        skills[s.name] = s
    return skills


def validate(skills: Dict[str, Skill], known_tools: Optional[Iterable[str]] = None) -> List[str]:
    """Return human-readable problems — unknown tool references, empty bodies.

    Called by the MCP server at import time so a typo'd tool name in a skill shows
    up in the server log rather than as a model hallucinating a tool that isn't
    there."""
    problems: List[str] = []
    known = set(known_tools) if known_tools is not None else None
    for s in skills.values():
        if not s.body.strip():
            problems.append(f"{s.name}: empty body")
        if known is not None:
            for t in s.tools:
                if t not in known:
                    problems.append(f"{s.name}: references unknown tool {t!r}")
    return problems


# ---------------------------------------------------------------------------
# The index — the only skill content that lives in context unconditionally
# ---------------------------------------------------------------------------

def index(skills: Dict[str, Skill]) -> List[Dict[str, object]]:
    return [s.index_entry() for s in skills.values()]


def index_text(skills: Dict[str, Skill]) -> str:
    """The index as a compact block for a system prompt (~1 line per skill)."""
    return "\n".join(f"- {s.name}: {s.description}" for s in skills.values())


# ---------------------------------------------------------------------------
# The lexical router — an optional pre-filter, not a decision-maker
# ---------------------------------------------------------------------------

def _idf(skills: Sequence[Skill]) -> Dict[str, float]:
    """Inverse document frequency over the catalog.

    With six skills this mostly does one job: kill the words every skill shares
    ('pump', 'radar', 'coin') so they stop dominating every score."""
    n = len(skills)
    df: Dict[str, int] = {}
    for s in skills:
        seen = set()
        for which in _FIELD_WEIGHTS:
            seen.update(_tokenize(s._field(which)))
        for t in seen:
            df[t] = df.get(t, 0) + 1
    return {t: math.log(1.0 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}


def score(query: str, skill: Skill, idf: Dict[str, float]) -> float:
    """BM25-ish score of one skill's metadata against a task string.

    Term frequency is saturated (tf/(tf+1)) so padding a keyword list with the same
    word nine times cannot outrank a skill actually *named* after it."""
    q = _tokenize(query)
    if not q:
        return 0.0
    total = 0.0
    for which, weight in _FIELD_WEIGHTS.items():
        toks = _tokenize(skill._field(which))
        if not toks:
            continue
        counts: Dict[str, int] = {}
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
        for t in set(q):
            tf = counts.get(t, 0)
            if tf:
                total += weight * idf.get(t, 1.0) * (tf / (tf + 1.0))
    return total


def route(
    query: str,
    skills: Dict[str, Skill],
    top_k: int = 3,
    min_score: float = 0.5,
) -> List[Tuple[Skill, float]]:
    """Shortlist the skills worth showing the model for this task, best first.

    Returns [] when nothing clears `min_score` — an honest "no skill fits", which
    the caller should treat as "hand the model the raw tools", never as "use the
    least-bad skill".  Forcing a procedure onto a task it wasn't written for is
    how routers produce confident nonsense."""
    ranked = sorted(skills.values(), key=lambda s: s.name)
    idfs = _idf(ranked)
    scored = [(s, score(query, s, idfs)) for s in ranked]
    scored = [(s, v) for s, v in scored if v >= min_score]
    scored.sort(key=lambda sv: (-sv[1], sv[0].name))
    return scored[: max(1, top_k)]


# ---------------------------------------------------------------------------
# CLI — inspect exactly what the model would be handed, without spending a token
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect the skill registry and its router.")
    ap.add_argument("--dir", default=str(SKILLS_DIR), help="skills directory")
    ap.add_argument("--list", action="store_true", help="print the index the model always sees")
    ap.add_argument("--route", metavar="TASK", help="shortlist skills for a task string")
    ap.add_argument("--show", metavar="NAME", help="print one skill's full procedure")
    ap.add_argument("--top", type=int, default=3, help="shortlist size for --route")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    skills = load_skills(args.dir)
    for p in validate(skills):
        print(f"warning: {p}")

    if args.show:
        s = skills.get(args.show)
        if not s:
            print(f"no such skill: {args.show}  (have: {', '.join(skills)})")
            return 1
        print(s.render())
        return 0

    if args.route:
        hits = route(args.route, skills, top_k=args.top)
        if args.json:
            print(json.dumps([{"name": s.name, "score": round(v, 3), "description": s.description}
                              for s, v in hits], indent=2))
        elif not hits:
            print("no skill matched — hand the model the raw tools instead")
        else:
            for s, v in hits:
                print(f"{v:6.2f}  {s.name:<22} {s.description}")
        return 0

    if args.json:
        print(json.dumps(index(skills), indent=2))
    else:
        print(index_text(skills))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
