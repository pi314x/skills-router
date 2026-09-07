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
    result = asyncio.run(lr.run_openai(None, [], "sys", "task", max_turns=1, verbose=False))
    assert result == "ok"
    assert recorder["init_kwargs"] == {"base_url": None, "api_key": None}
    assert recorder["create_kwargs"]["model"] == lr.OPENAI_MODEL


def test_run_openrouter_targets_openrouter_base_url_and_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    recorder = {}
    _fake_openai_client(monkeypatch, recorder)
    result = asyncio.run(lr.run_openrouter(None, [], "sys", "task", max_turns=1, verbose=False))
    assert result == "ok"
    assert recorder["init_kwargs"] == {"base_url": lr.OPENROUTER_BASE_URL, "api_key": "sk-or-test"}
    assert recorder["create_kwargs"]["model"] == lr.OPENROUTER_MODEL


def test_run_openrouter_honors_model_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(lr, "OPENROUTER_MODEL", "anthropic/claude-opus-5")
    recorder = {}
    _fake_openai_client(monkeypatch, recorder)
    asyncio.run(lr.run_openrouter(None, [], "sys", "task", max_turns=1, verbose=False))
    assert recorder["create_kwargs"]["model"] == "anthropic/claude-opus-5"


def test_run_gemini_targets_gemini_base_url_and_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    recorder = {}
    _fake_openai_client(monkeypatch, recorder)
    result = asyncio.run(lr.run_gemini(None, [], "sys", "task", max_turns=1, verbose=False))
    assert result == "ok"
    assert recorder["init_kwargs"] == {"base_url": lr.GEMINI_BASE_URL, "api_key": "gem-test"}
    assert recorder["create_kwargs"]["model"] == lr.GEMINI_MODEL


def test_run_gemini_honors_model_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.setattr(lr, "GEMINI_MODEL", "gemini-2.5-pro")
    recorder = {}
    _fake_openai_client(monkeypatch, recorder)
    asyncio.run(lr.run_gemini(None, [], "sys", "task", max_turns=1, verbose=False))
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
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = FakeChat()

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    result = asyncio.run(lr.run_openai(FakeFleet(), [], "sys", "task", max_turns=3, verbose=False))
    assert result == "done"
    assert calls == [("get_skill", {"name": "triage-alert"})]


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
        provider=provider, mode="local", routing="model", task="some task",
        mcp_url=None, max_turns=1, tool_search=False, verbose=False)
    assert asyncio.run(lr.run(args)) == 0
    assert called == [expected]
    assert capsys.readouterr().out.strip() == "answer"
