"""Offline tests for llm_router.py's provider wiring — Claude, OpenAI, OpenRouter, Gemini.

No network and no real API keys: every LLM call is replaced with a fake that just
records what it was asked to send. The point is to prove the *wiring* is right —
which base URL, which API key, which model string reaches the client — since that
is the only thing that can silently break when a new provider is bolted on. Actual
prompt/response quality is out of scope here; USE_AI gates that behind real money.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import llm_router as lr


# ----------------------------------------------------------------- ai_enabled

@pytest.mark.parametrize("value,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("", False),
])
def test_ai_enabled_reads_use_ai(monkeypatch, value, expected):
    monkeypatch.setenv("USE_AI", value)
    assert lr.ai_enabled() is expected


def test_ai_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("USE_AI", raising=False)
    assert lr.ai_enabled() is False


# ----------------------------------------------------------------- pick_provider

def _clear_provider_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_pick_provider_explicit_wins(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert lr.pick_provider("claude") == "claude"


def test_pick_provider_prefers_anthropic_first(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "b")
    monkeypatch.setenv("OPENROUTER_API_KEY", "c")
    monkeypatch.setenv("GEMINI_API_KEY", "d")
    assert lr.pick_provider(None) == "claude"


def test_pick_provider_falls_back_to_openai(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "b")
    monkeypatch.setenv("OPENROUTER_API_KEY", "c")
    monkeypatch.setenv("GEMINI_API_KEY", "d")
    assert lr.pick_provider(None) == "openai"


def test_pick_provider_falls_back_to_openrouter(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "c")
    monkeypatch.setenv("GEMINI_API_KEY", "d")
    assert lr.pick_provider(None) == "openrouter"


def test_pick_provider_falls_back_to_gemini(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "d")
    assert lr.pick_provider(None) == "gemini"


def test_pick_provider_defaults_to_claude_with_no_keys(monkeypatch):
    _clear_provider_env(monkeypatch)
    assert lr.pick_provider(None) == "claude"


# ----------------------------------------------------------------- tool translation

def test_to_openai_tools_shape():
    specs = [lr.ToolSpec("get_skill", "load a skill", {"type": "object", "properties": {}})]
    tools = lr.to_openai_tools(specs)
    assert tools == [{
        "type": "function",
        "function": {"name": "get_skill", "description": "load a skill",
                     "parameters": {"type": "object", "properties": {}}},
    }]


def test_to_anthropic_tools_shape():
    specs = [lr.ToolSpec("get_skill", "load a skill", {"type": "object", "properties": {}})]
    tools = lr.to_anthropic_tools(specs)
    assert tools == [{"name": "get_skill", "description": "load a skill",
                      "input_schema": {"type": "object", "properties": {}}}]
    assert "defer_loading" not in tools[0]


def test_to_anthropic_tools_defers_data_tools_but_not_meta_tools():
    specs = [lr.ToolSpec("list_skills", "", {}), lr.ToolSpec("some_data_tool", "", {})]
    tools = lr.to_anthropic_tools(specs, defer=True)
    by_name = {t["name"]: t for t in tools}
    assert "defer_loading" not in by_name["list_skills"]
    assert by_name["some_data_tool"]["defer_loading"] is True


def test_tool_spec_defaults_when_empty():
    spec = lr.ToolSpec("x", "", None)
    assert spec.description == ""
    assert spec.schema == {"type": "object", "properties": {}}


# ----------------------------------------------------------------- token accounting

def test_token_counts_reads_the_openai_shape():
    usage = SimpleNamespace(prompt_tokens=1204, completion_tokens=88, total_tokens=1292)
    assert lr.token_counts(usage) == (1204, 88)


def test_token_counts_reads_the_anthropic_shape():
    usage = SimpleNamespace(input_tokens=900, output_tokens=88,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0)
    assert lr.token_counts(usage) == (900, 88)


def test_token_counts_adds_anthropic_cache_tokens_to_input():
    """Anthropic reports cache hits outside input_tokens.

    Left out, a cached run looks far cheaper than the prompt it actually sent —
    which would invert the very comparison this counter exists to make."""
    usage = SimpleNamespace(input_tokens=100, output_tokens=20,
                            cache_read_input_tokens=4000, cache_creation_input_tokens=50)
    assert lr.token_counts(usage) == (4150, 20)


def test_token_counts_survives_a_provider_that_reports_nothing():
    """Some gateways omit usage entirely — that is not a reason to fail a good run."""
    assert lr.token_counts(None) == (0, 0)
    assert lr.token_counts(SimpleNamespace()) == (0, 0)
    assert lr.token_counts(SimpleNamespace(prompt_tokens=12, completion_tokens=None)) == (12, 0)


def test_usage_accumulates_and_reports():
    usage = lr.Usage()
    usage.record(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10)),
                 turn=0, verbose=False)
    usage.record(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=250, completion_tokens=30)),
                 turn=1, verbose=False)
    assert (usage.input, usage.output, usage.turns) == (350, 40, 2)
    assert usage.report("prefilter") == "tokens: in=350 out=40 total=390 turns=2 routing=prefilter"


def test_usage_logs_each_turn_only_when_verbose(capsys):
    usage = lr.Usage()
    resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3))
    usage.record(resp, turn=0, verbose=False)
    assert capsys.readouterr().err == ""
    usage.record(resp, turn=1, verbose=True)
    assert "[1] tokens in=7 out=3" in capsys.readouterr().err


# ----------------------------------------------------------------- OpenAI-compatible loop

def _fake_openai_client(monkeypatch, recorder, reply="ok"):
    """Patch openai.AsyncOpenAI with a client that records init/call kwargs and
    returns a single non-tool-call reply, so the loop exits after one turn."""

    class FakeCompletions:
        async def create(self, **kwargs):
            recorder["create_kwargs"] = kwargs
            message = SimpleNamespace(content=reply, tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            recorder["init_kwargs"] = kwargs
            self.chat = FakeChat()

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)


def test_run_openai_uses_default_model_and_no_explicit_base_url(monkeypatch):
    recorder = {}
    _fake_openai_client(monkeypatch, recorder)
    result = asyncio.run(lr.run_openai(None, [], "sys", "task", usage=lr.Usage(), max_turns=1, verbose=False))
    assert result == "ok"
    assert recorder["init_kwargs"] == {"base_url": None, "api_key": None}
    assert recorder["create_kwargs"]["model"] == lr.OPENAI_MODEL


def test_run_openrouter_targets_openrouter_base_url_and_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    recorder = {}
    _fake_openai_client(monkeypatch, recorder)
    result = asyncio.run(lr.run_openrouter(None, [], "sys", "task", usage=lr.Usage(), max_turns=1, verbose=False))
    assert result == "ok"
    assert recorder["init_kwargs"] == {"base_url": lr.OPENROUTER_BASE_URL, "api_key": "sk-or-test"}
    assert recorder["create_kwargs"]["model"] == lr.OPENROUTER_MODEL


def test_run_openrouter_honors_model_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(lr, "OPENROUTER_MODEL", "anthropic/claude-opus-5")
    recorder = {}
    _fake_openai_client(monkeypatch, recorder)
    asyncio.run(lr.run_openrouter(None, [], "sys", "task", usage=lr.Usage(), max_turns=1, verbose=False))
    assert recorder["create_kwargs"]["model"] == "anthropic/claude-opus-5"


def test_run_gemini_targets_gemini_base_url_and_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    recorder = {}
    _fake_openai_client(monkeypatch, recorder)
    result = asyncio.run(lr.run_gemini(None, [], "sys", "task", usage=lr.Usage(), max_turns=1, verbose=False))
    assert result == "ok"
    assert recorder["init_kwargs"] == {"base_url": lr.GEMINI_BASE_URL, "api_key": "gem-test"}
    assert recorder["create_kwargs"]["model"] == lr.GEMINI_MODEL


def test_run_gemini_honors_model_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.setattr(lr, "GEMINI_MODEL", "gemini-2.5-pro")
    recorder = {}
    _fake_openai_client(monkeypatch, recorder)
    asyncio.run(lr.run_gemini(None, [], "sys", "task", usage=lr.Usage(), max_turns=1, verbose=False))
    assert recorder["create_kwargs"]["model"] == "gemini-2.5-pro"


def test_run_openai_forwards_tool_calls_to_the_fleet(monkeypatch):
    """A tool_calls turn must reach fleet.call before the loop returns — this is
    the one piece of behavior OpenRouter reuses unmodified from the OpenAI path."""
    calls = []

    class FakeFleet:
        async def call(self, name, args):
            calls.append((name, args))
            return '{"ok": true}'

    turns = {"n": 0}

    class FakeCompletions:
        async def create(self, **kwargs):
            turns["n"] += 1
            if turns["n"] == 1:
                tool_call = SimpleNamespace(
                    id="call_1",
                    function=SimpleNamespace(name="get_skill", arguments='{"name": "triage-alert"}'),
                )
                message = SimpleNamespace(
                    content=None, tool_calls=[tool_call],
                    model_dump=lambda exclude_none=True: {"role": "assistant", "tool_calls": [tool_call]},
                )
            else:
                message = SimpleNamespace(content="done", tool_calls=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)],
                usage=SimpleNamespace(prompt_tokens=100 * turns["n"], completion_tokens=10),
            )

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = FakeChat()

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    usage = lr.Usage()
    result = asyncio.run(lr.run_openai(FakeFleet(), [], "sys", "task", usage=usage,
                                       max_turns=3, verbose=False))
    assert result == "done"
    assert calls == [("get_skill", {"name": "triage-alert"})]
    # Every turn counts, the tool-calling one included — not just the turn that answered.
    assert (usage.input, usage.output, usage.turns) == (300, 20, 2)


# ----------------------------------------------------------------- Claude loop

def test_run_claude_counts_a_paused_turn(monkeypatch):
    """A pause_turn response was generated and billed before we resend.

    Counting it only after the pause check would undercount exactly the runs that
    lean on server-side tools — the ones most likely to pause."""
    replies = [
        SimpleNamespace(stop_reason="pause_turn", content=[],
                        usage=SimpleNamespace(input_tokens=500, output_tokens=40,
                                              cache_read_input_tokens=0,
                                              cache_creation_input_tokens=0)),
        SimpleNamespace(stop_reason="end_turn",
                        content=[SimpleNamespace(type="text", text="the answer")],
                        usage=SimpleNamespace(input_tokens=600, output_tokens=60,
                                              cache_read_input_tokens=0,
                                              cache_creation_input_tokens=0)),
    ]

    class FakeMessages:
        async def create(self, **kwargs):
            return replies.pop(0)

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.AsyncAnthropic", FakeAsyncAnthropic)
    usage = lr.Usage()
    result = asyncio.run(lr.run_claude(None, [], "sys", "task", usage=usage,
                                       max_turns=3, tool_search=False, verbose=False))
    assert result == "the answer"
    assert (usage.input, usage.output, usage.turns) == (1100, 100, 2)


# ----------------------------------------------------------------- CLI plumbing

@pytest.mark.parametrize("provider", ["openrouter", "gemini"])
def test_provider_flag_accepts_new_providers(monkeypatch, capsys, provider):
    monkeypatch.delenv("USE_AI", raising=False)
    monkeypatch.setattr(
        "sys.argv", ["llm_router.py", "--provider", provider, "some task"])
    # USE_AI is unset, so main() must reject *after* successfully parsing
    # --provider <provider> — an invalid argparse choice would exit(2) earlier
    # with a "invalid choice" usage error instead of this message.
    assert lr.main() == 2
    assert "USE_AI" in capsys.readouterr().err


def _no_dial_out(monkeypatch):
    """Trip-wire for the USE_AI=true tests below.

    They exercise guards that are supposed to reject *before* main() reaches
    asyncio.run.  Should a guard ever regress, main() would connect to
    DEFAULT_MCP_URLS and — with a key in the environment — bill a real call.
    Failing the test is the correct outcome there, not dialing out."""
    def boom(*_a, **_kw):
        raise AssertionError("main() proceeded past its guards into asyncio.run")

    monkeypatch.setattr(lr.asyncio, "run", boom)


@pytest.mark.parametrize("provider", ["openrouter", "gemini"])
def test_tool_search_flag_rejected_for_gateway_providers(monkeypatch, capsys, provider):
    monkeypatch.setenv("USE_AI", "true")
    _clear_provider_env(monkeypatch)
    _no_dial_out(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        ["llm_router.py", "--provider", provider, "--tool-search", "some task"])
    assert lr.main() == 2
    assert "tool-search" in capsys.readouterr().err


@pytest.mark.parametrize("provider", ["openrouter", "gemini"])
def test_mode_connector_rejected_for_gateway_providers(monkeypatch, capsys, provider):
    monkeypatch.setenv("USE_AI", "true")
    _clear_provider_env(monkeypatch)
    _no_dial_out(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        ["llm_router.py", "--provider", provider, "--mode", "connector", "some task"])
    assert lr.main() == 2
    assert "connector" in capsys.readouterr().err


@pytest.mark.parametrize("provider,env_var", [
    ("openrouter", "OPENROUTER_API_KEY"),
    ("gemini", "GEMINI_API_KEY"),
])
def test_missing_gateway_key_rejected_before_connecting(monkeypatch, capsys, provider, env_var):
    """The key check must fire in main(), not deep inside run().

    Checked at the far end of run() it costs an MCP session first, and under
    --routing prefilter the routing calls that session already spent — and it
    surfaces as exit 1 behind a 'cannot reach MCP server' error rather than the
    exit 2 every other config mistake here returns."""
    monkeypatch.setenv("USE_AI", "true")
    _clear_provider_env(monkeypatch)
    _no_dial_out(monkeypatch)
    monkeypatch.setattr("sys.argv", ["llm_router.py", "--provider", provider, "some task"])
    assert lr.main() == 2
    assert env_var in capsys.readouterr().err


def test_blank_gateway_key_counts_as_missing(monkeypatch, capsys):
    """A commented-out .env line left as `OPENROUTER_API_KEY=` is not a key."""
    monkeypatch.setenv("USE_AI", "true")
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
    _no_dial_out(monkeypatch)
    monkeypatch.setattr("sys.argv", ["llm_router.py", "--provider", "openrouter", "some task"])
    assert lr.main() == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


def test_compare_routing_rejected_with_connector_mode(monkeypatch, capsys):
    """run()'s connector branch returns before ever consulting compare_routing —
    silently running one strategy under --compare-routing would be worse than
    refusing, since the output looks like a comparison but isn't one."""
    monkeypatch.setenv("USE_AI", "true")
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    _no_dial_out(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        ["llm_router.py", "--compare-routing", "--mode", "connector", "some task"])
    assert lr.main() == 2
    assert "--compare-routing" in capsys.readouterr().err


# ----------------------------------------------------------------- run() dispatch

@pytest.mark.parametrize("provider,expected", [
    ("claude", "run_claude"),
    ("openai", "run_openai"),
    ("openrouter", "run_openrouter"),
    ("gemini", "run_gemini"),
])
def test_run_dispatches_to_the_right_provider(monkeypatch, capsys, provider, expected):
    """Each provider must reach its own runner.

    Without this, dropping a branch from run()'s dispatch chain sends the request
    to the `else` arm — OpenAI's endpoint, model and key — with every other test
    still green and only the bill to show for it."""
    called = []

    @asynccontextmanager
    async def fake_connect(urls):
        yield SimpleNamespace(specs=[], collisions=[], sessions=[])

    async def fake_build_system_prompt(fleet, task, routing):
        return "sys", None

    def recorder(name):
        async def run_it(*_a, **_kw):
            called.append(name)
            return "answer"
        return run_it

    monkeypatch.setattr(lr, "connect", fake_connect)
    monkeypatch.setattr(lr, "build_system_prompt", fake_build_system_prompt)
    for name in ("run_claude", "run_openai", "run_openrouter", "run_gemini"):
        monkeypatch.setattr(lr, name, recorder(name))

    args = argparse.Namespace(
        provider=provider, mode="local", routing="model", compare_routing=False,
        task="some task", mcp_url=None, max_turns=1, tool_search=False, verbose=False)
    assert asyncio.run(lr.run(args)) == 0
    assert called == [expected]
    assert capsys.readouterr().out.strip() == "answer"


def test_compare_routing_runs_all_three_strategies_and_tables_the_totals(monkeypatch, capsys):
    """--compare-routing must build a fresh prompt per strategy (so each gets its
    own routing decision, not the same one three times) and report every one's
    cost, not just the last."""
    @asynccontextmanager
    async def fake_connect(urls):
        yield SimpleNamespace(specs=[], collisions=[], sessions=[])

    seen_routings = []

    async def fake_build_system_prompt(fleet, task, routing):
        seen_routings.append(routing)
        skill = "triage-alert" if routing == "prefilter" else None
        return f"sys-for-{routing}", skill

    # Distinct, easily-checked token counts per routing so a wrong row is obvious.
    costs = {"model": (1000, 100), "prefilter": (400, 50), "none": (2000, 200)}

    async def fake_run_openai(fleet, specs, system, task, *, usage, **_kw):
        routing = system.removeprefix("sys-for-")
        inp, out = costs[routing]
        usage.record(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=inp,
                                                            completion_tokens=out)),
                     turn=0, verbose=False)
        return f"answer-for-{routing}"

    monkeypatch.setattr(lr, "connect", fake_connect)
    monkeypatch.setattr(lr, "build_system_prompt", fake_build_system_prompt)
    monkeypatch.setattr(lr, "run_openai", fake_run_openai)

    args = argparse.Namespace(
        provider="openai", mode="local", routing="model", compare_routing=True,
        task="some task", mcp_url=None, max_turns=1, tool_search=False, verbose=False)
    assert asyncio.run(lr.run(args)) == 0

    assert seen_routings == ["model", "prefilter", "none"]
    out = capsys.readouterr().out
    for routing in ("model", "prefilter", "none"):
        assert f"answer-for-{routing}" in out
    assert "skill=triage-alert" in out          # only prefilter's section names one
    lines = {line.split()[0]: line for line in out.splitlines()
            if line.split()[:1] and line.split()[0] in costs}
    assert lines["model"].split()[1:] == ["1000", "100", "1100", "1"]
    assert lines["prefilter"].split()[1:] == ["400", "50", "450", "1"]
    assert lines["none"].split()[1:] == ["2000", "200", "2200", "1"]


def test_run_reports_the_token_total_when_verbose(monkeypatch, capsys):
    """The run total is the headline number, so run() must print it once —
    and only on --verbose, where the rest of the diagnostics already live."""
    @asynccontextmanager
    async def fake_connect(urls):
        yield SimpleNamespace(specs=[], collisions=[], sessions=[])

    async def fake_build_system_prompt(fleet, task, routing):
        return "sys", None

    async def fake_run_openai(fleet, specs, system, task, *, usage, **_kw):
        usage.record(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1204,
                                                           completion_tokens=88)),
                     turn=0, verbose=False)
        return "answer"

    monkeypatch.setattr(lr, "connect", fake_connect)
    monkeypatch.setattr(lr, "build_system_prompt", fake_build_system_prompt)
    monkeypatch.setattr(lr, "run_openai", fake_run_openai)

    def call(verbose):
        args = argparse.Namespace(
            provider="openai", mode="local", routing="prefilter", compare_routing=False,
            task="some task", mcp_url=None, max_turns=1, tool_search=False, verbose=verbose)
        assert asyncio.run(lr.run(args)) == 0
        return capsys.readouterr()

    quiet = call(verbose=False)
    assert "tokens:" not in quiet.err
    assert quiet.out.strip() == "answer"

    loud = call(verbose=True)
    assert "tokens: in=1204 out=88 total=1292 turns=1 routing=prefilter" in loud.err
    # The answer still goes to stdout alone, so `... > answer.txt` stays clean.
    assert loud.out.strip() == "answer"
