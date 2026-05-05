"""Tests for the A2A (Agent-to-Agent) protocol client support.

All tests use mocks -- no real A2A servers are contacted.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent_card(name="test-agent", description="A test agent", url="http://localhost:9999"):
    """Create a fake AgentCard object."""
    skill = SimpleNamespace(
        id="skill-1",
        name="test-skill",
        description="A test skill",
    )
    capabilities = SimpleNamespace(
        streaming=True,
        pushNotifications=False,
    )
    card = SimpleNamespace(
        name=name,
        description=description,
        url=url,
        skills=[skill],
        capabilities=capabilities,
        securitySchemes={"bearer": {"type": "http", "scheme": "bearer"}},
    )
    return card


def _make_send_response(text="Hello from remote agent", status="completed"):
    """Create a fake SendMessageResponse."""
    text_part = SimpleNamespace(text=text, root=SimpleNamespace(text=text))
    message = SimpleNamespace(parts=[text_part])
    task_status = SimpleNamespace(state=status, message=message)
    artifact = SimpleNamespace(parts=[text_part])
    task = SimpleNamespace(status=task_status, artifacts=[artifact])
    root = SimpleNamespace(result=task, error=None)
    return SimpleNamespace(root=root)


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

class TestA2AAvailability:
    def test_check_returns_bool(self):
        """check_a2a_available returns a boolean."""
        from tools.a2a_tool import check_a2a_available
        result = check_a2a_available()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

class TestResolveAgentURL:
    def test_direct_url_http(self):
        """Direct HTTP URL passes through."""
        from tools.a2a_tool import _resolve_agent_url
        with patch("tools.a2a_tool._load_a2a_config", return_value={}):
            url, config = _resolve_agent_url("http://localhost:9999")
            assert url == "http://localhost:9999"
            assert config == {}

    def test_direct_url_https(self):
        """Direct HTTPS URL passes through."""
        from tools.a2a_tool import _resolve_agent_url
        with patch("tools.a2a_tool._load_a2a_config", return_value={}):
            url, config = _resolve_agent_url("https://agent.example.com")
            assert url == "https://agent.example.com"

    def test_trailing_slash_stripped(self):
        """Trailing slash is removed from URLs."""
        from tools.a2a_tool import _resolve_agent_url
        with patch("tools.a2a_tool._load_a2a_config", return_value={}):
            url, _ = _resolve_agent_url("http://localhost:9999/")
            assert url == "http://localhost:9999"

    def test_config_name_resolved(self):
        """Named agent from config resolves to its URL."""
        config = {
            "researcher": {
                "url": "http://researcher.local:8080",
                "auth": {"type": "bearer", "token": "test-token"},
            }
        }
        from tools.a2a_tool import _resolve_agent_url
        with patch("tools.a2a_tool._load_a2a_config", return_value=config):
            url, agent_config = _resolve_agent_url("researcher")
            assert url == "http://researcher.local:8080"
            assert agent_config["auth"]["type"] == "bearer"

    def test_unknown_name_raises(self):
        """Unknown agent name raises ValueError."""
        from tools.a2a_tool import _resolve_agent_url
        with patch("tools.a2a_tool._load_a2a_config", return_value={}):
            with pytest.raises(ValueError, match="Unknown A2A agent"):
                _resolve_agent_url("nonexistent")

    def test_config_missing_url_raises(self):
        """Config entry without URL raises ValueError."""
        config = {"broken": {"auth": {"type": "bearer"}}}
        from tools.a2a_tool import _resolve_agent_url
        with patch("tools.a2a_tool._load_a2a_config", return_value=config):
            with pytest.raises(ValueError, match="no 'url'"):
                _resolve_agent_url("broken")


# ---------------------------------------------------------------------------
# Agent Card formatting
# ---------------------------------------------------------------------------

class TestFormatAgentCard:
    def test_basic_card(self):
        """Agent Card is formatted with all fields."""
        from tools.a2a_tool import _format_agent_card
        card = _make_agent_card()
        result = _format_agent_card(card)
        assert result["name"] == "test-agent"
        assert result["description"] == "A test agent"
        assert len(result["skills"]) == 1
        assert result["skills"][0]["name"] == "test-skill"
        assert result["capabilities"]["streaming"] is True
        assert "bearer" in result["auth_schemes"]

    def test_card_without_skills(self):
        """Card with no skills still formats correctly."""
        from tools.a2a_tool import _format_agent_card
        card = SimpleNamespace(
            name="minimal",
            description="",
            url="http://localhost",
            skills=None,
            capabilities=None,
            securitySchemes=None,
        )
        result = _format_agent_card(card)
        assert result["name"] == "minimal"
        assert "skills" not in result


# ---------------------------------------------------------------------------
# Response extraction
# ---------------------------------------------------------------------------

class TestExtractResponse:
    def test_successful_response(self):
        """Completed task extracts text from artifacts."""
        from tools.a2a_tool import _extract_response
        response = _make_send_response(text="Result text", status="completed")
        result = _extract_response(response)
        assert result["status"] == "completed"
        assert "Result text" in result.get("response", "")

    def test_error_response(self):
        """Error in response is captured."""
        from tools.a2a_tool import _extract_response
        error_response = SimpleNamespace(
            root=SimpleNamespace(
                error=SimpleNamespace(message="Something went wrong"),
                result=None,
            )
        )
        result = _extract_response(error_response)
        assert result["status"] == "error"

    def test_input_required(self):
        """INPUT_REQUIRED status returns the agent's question."""
        from tools.a2a_tool import _extract_response
        response = _make_send_response(
            text="What file should I read?",
            status="input-required",
        )
        result = _extract_response(response)
        assert result["status"] == "input-required"
        assert "What file" in result["response"]


# ---------------------------------------------------------------------------
# Tool handler: a2a_discover
# ---------------------------------------------------------------------------

class TestA2ADiscover:
    def test_missing_agent_param(self):
        """Missing agent parameter returns error."""
        from tools.a2a_tool import a2a_discover
        result = json.loads(a2a_discover({}))
        assert "error" in result
        assert "Missing" in result["error"]

    def test_empty_agent_param(self):
        """Empty agent string returns error."""
        from tools.a2a_tool import a2a_discover
        result = json.loads(a2a_discover({"agent": "  "}))
        assert "error" in result

    @patch("tools.a2a_tool._run_on_loop")
    @patch("tools.a2a_tool._resolve_agent_url")
    def test_successful_discover(self, mock_resolve, mock_run):
        """Successful discovery returns formatted card."""
        from tools.a2a_tool import a2a_discover
        mock_resolve.return_value = ("http://localhost:9999", {})
        mock_run.return_value = {
            "name": "test-agent",
            "description": "A test agent",
            "skills": [{"id": "1", "name": "test", "description": "test skill"}],
        }
        result = json.loads(a2a_discover({"agent": "http://localhost:9999"}))
        assert result["name"] == "test-agent"
        assert len(result["skills"]) == 1

    @patch("tools.a2a_tool._run_on_loop")
    @patch("tools.a2a_tool._resolve_agent_url")
    def test_discover_connection_error(self, mock_resolve, mock_run):
        """Connection error returns sanitized message."""
        from tools.a2a_tool import a2a_discover
        mock_resolve.return_value = ("http://unreachable:9999", {})
        mock_run.side_effect = ConnectionError("Connection refused")
        result = json.loads(a2a_discover({"agent": "http://unreachable:9999"}))
        assert "error" in result
        assert "Failed to discover" in result["error"]


# ---------------------------------------------------------------------------
# Tool handler: a2a_call
# ---------------------------------------------------------------------------

class TestA2ACall:
    def test_missing_agent(self):
        """Missing agent returns error."""
        from tools.a2a_tool import a2a_call
        result = json.loads(a2a_call({"message": "hello"}))
        assert "error" in result
        assert "agent" in result["error"].lower()

    def test_missing_message(self):
        """Missing message returns error."""
        from tools.a2a_tool import a2a_call
        result = json.loads(a2a_call({"agent": "http://localhost:9999"}))
        assert "error" in result
        assert "message" in result["error"].lower()

    @patch("tools.a2a_tool._run_on_loop")
    @patch("tools.a2a_tool._resolve_agent_url")
    def test_successful_call(self, mock_resolve, mock_run):
        """Successful call returns agent response."""
        from tools.a2a_tool import a2a_call
        mock_resolve.return_value = ("http://localhost:9999", {})
        mock_run.return_value = json.dumps({
            "status": "completed",
            "response": "Hello from the agent!",
        })
        result = json.loads(a2a_call({
            "agent": "http://localhost:9999",
            "message": "Say hello",
        }))
        assert result["status"] == "completed"
        assert "Hello" in result["response"]

    @patch("tools.a2a_tool._run_on_loop")
    @patch("tools.a2a_tool._resolve_agent_url")
    def test_call_with_config_name(self, mock_resolve, mock_run):
        """Call using config name resolves correctly."""
        from tools.a2a_tool import a2a_call
        mock_resolve.return_value = ("http://researcher.local:8080", {"timeout": 60})
        mock_run.return_value = json.dumps({"status": "completed", "response": "Done"})
        result = json.loads(a2a_call({
            "agent": "researcher",
            "message": "Research topic X",
        }))
        assert result["status"] == "completed"
        mock_resolve.assert_called_once_with("researcher")

    @patch("tools.a2a_tool._run_on_loop")
    @patch("tools.a2a_tool._resolve_agent_url")
    def test_call_error_sanitized(self, mock_resolve, mock_run):
        """Credentials are stripped from error messages."""
        from tools.a2a_tool import a2a_call
        mock_resolve.return_value = ("http://localhost:9999", {})
        mock_run.side_effect = Exception("Auth failed with Bearer sk-secret123abc")
        result = json.loads(a2a_call({
            "agent": "http://localhost:9999",
            "message": "hello",
        }))
        assert "error" in result
        assert "sk-secret123abc" not in result["error"]
        assert "[REDACTED]" in result["error"]


# ---------------------------------------------------------------------------
# Credential sanitization
# ---------------------------------------------------------------------------

class TestSanitizeError:
    def test_bearer_token(self):
        """Bearer tokens are redacted."""
        from tools.a2a_tool import _sanitize_error
        assert "[REDACTED]" in _sanitize_error("Bearer sk-abc123xyz")

    def test_api_key(self):
        """sk- prefixed keys are redacted."""
        from tools.a2a_tool import _sanitize_error
        result = _sanitize_error("Failed with key sk-mySecretKey123")
        assert "sk-mySecretKey123" not in result

    def test_clean_text_unchanged(self):
        """Text without credentials passes through."""
        from tools.a2a_tool import _sanitize_error
        text = "Connection refused to localhost:9999"
        assert _sanitize_error(text) == text


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_tools_registered(self):
        """Both A2A tools are registered in the registry."""
        from tools.registry import registry
        import tools.a2a_tool  # noqa: F401 -- triggers registration

        names = registry.get_all_tool_names()
        assert "a2a_discover" in names
        assert "a2a_call" in names

    def test_toolset_assignment(self):
        """Both tools belong to the 'a2a' toolset."""
        from tools.registry import registry
        import tools.a2a_tool  # noqa: F401

        assert registry.get_toolset_for_tool("a2a_discover") == "a2a"
        assert registry.get_toolset_for_tool("a2a_call") == "a2a"

    def test_schemas_valid(self):
        """Tool schemas have required fields."""
        from tools.a2a_tool import A2A_DISCOVER_SCHEMA, A2A_CALL_SCHEMA

        assert A2A_DISCOVER_SCHEMA["name"] == "a2a_discover"
        assert "parameters" in A2A_DISCOVER_SCHEMA
        assert "agent" in A2A_DISCOVER_SCHEMA["parameters"]["properties"]

        assert A2A_CALL_SCHEMA["name"] == "a2a_call"
        assert "agent" in A2A_CALL_SCHEMA["parameters"]["properties"]
        assert "message" in A2A_CALL_SCHEMA["parameters"]["properties"]
        assert "stream" in A2A_CALL_SCHEMA["parameters"]["properties"]


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

class TestFormatCallResponse:
    def test_empty_results(self):
        """Empty results return a default message."""
        from tools.a2a_tool import _format_call_response
        result = json.loads(_format_call_response([]))
        assert result["status"] == "completed"
        assert "empty" in result["response"].lower()

    def test_multiple_chunks(self):
        """Multiple streaming chunks are joined."""
        from tools.a2a_tool import _format_call_response
        result = json.loads(_format_call_response(["chunk1", "chunk2", "chunk3"]))
        assert "chunk1" in result["response"]
        assert "chunk2" in result["response"]
        assert "chunk3" in result["response"]


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestLoadConfig:
    @patch("tools.a2a_tool.os.path.exists", return_value=False)
    def test_no_config_file(self, mock_exists):
        """Missing config file returns empty dict."""
        import tools.a2a_tool as mod
        mod._config_cache = None  # Reset cache
        result = mod._load_a2a_config()
        assert result == {}

    def test_config_caching(self):
        """Config is cached after first load."""
        import tools.a2a_tool as mod
        mod._config_cache = {"cached": {"url": "http://cached"}}
        result = mod._load_a2a_config()
        assert "cached" in result
        mod._config_cache = None  # Cleanup


# ===========================================================================
# Phase 3: Multi-Agent Orchestration
# ===========================================================================

# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------

class TestBuildAgentRegistry:
    def test_empty_registry(self):
        """No config + no discovered cards = empty list."""
        from tools.a2a_tool import _build_agent_registry
        with patch("tools.a2a_tool._load_a2a_config", return_value={}):
            with patch("tools.a2a_tool._agent_cards", {}):
                result = _build_agent_registry()
                assert result == []

    def test_config_only(self):
        """Config agents appear even without discovery."""
        from tools.a2a_tool import _build_agent_registry
        config = {
            "researcher": {"url": "http://researcher:8080"},
            "coder": {"url": "http://coder:9090"},
        }
        with patch("tools.a2a_tool._load_a2a_config", return_value=config):
            with patch("tools.a2a_tool._agent_cards", {}):
                result = _build_agent_registry()
                assert len(result) == 2
                names = {a["name"] for a in result}
                assert names == {"researcher", "coder"}
                assert all(a["source"] == "config" for a in result)
                assert all(a["status"] == "configured" for a in result)

    def test_discovered_only(self):
        """Discovered agents not in config appear as source=discovered."""
        from tools.a2a_tool import _build_agent_registry
        card = _make_agent_card(name="remote-agent", url="http://remote:5555")
        with patch("tools.a2a_tool._load_a2a_config", return_value={}):
            with patch("tools.a2a_tool._agent_cards", {"http://remote:5555": card}):
                result = _build_agent_registry()
                assert len(result) == 1
                assert result[0]["name"] == "remote-agent"
                assert result[0]["source"] == "discovered"
                assert len(result[0]["skills"]) == 1

    def test_merged_config_and_discovered(self):
        """Config agent that's been discovered shows status=discovered with skills."""
        from tools.a2a_tool import _build_agent_registry
        card = _make_agent_card(name="researcher-card", url="http://researcher:8080")
        config = {"researcher": {"url": "http://researcher:8080"}}
        with patch("tools.a2a_tool._load_a2a_config", return_value=config):
            with patch("tools.a2a_tool._agent_cards", {"http://researcher:8080": card}):
                result = _build_agent_registry()
                assert len(result) == 1
                assert result[0]["name"] == "researcher"
                assert result[0]["source"] == "config"
                assert result[0]["status"] == "discovered"
                assert len(result[0]["skills"]) == 1


# ---------------------------------------------------------------------------
# Skill matching
# ---------------------------------------------------------------------------

class TestMatchSkillsToGoal:
    def test_exact_keyword_match(self):
        """Keywords in goal that match skill names score > 0."""
        from tools.a2a_tool import _match_skills_to_goal
        info = {
            "description": "Research assistant",
            "skills": [{"name": "research", "description": "web research", "id": "s1"}],
        }
        score = _match_skills_to_goal("Do some research on AI", info)
        assert score > 0.0

    def test_no_match(self):
        """Completely unrelated goal scores 0."""
        from tools.a2a_tool import _match_skills_to_goal
        info = {
            "description": "Music player",
            "skills": [{"name": "play-music", "description": "plays songs", "id": "s1"}],
        }
        score = _match_skills_to_goal("Deploy kubernetes cluster", info)
        assert score == 0.0

    def test_empty_goal(self):
        """Empty goal returns 0."""
        from tools.a2a_tool import _match_skills_to_goal
        info = {"description": "anything", "skills": []}
        assert _match_skills_to_goal("", info) == 0.0

    def test_no_skills_no_description(self):
        """Agent with empty skills/description scores 0."""
        from tools.a2a_tool import _match_skills_to_goal
        info = {"description": "", "skills": []}
        assert _match_skills_to_goal("research AI topics", info) == 0.0

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        from tools.a2a_tool import _match_skills_to_goal
        info = {
            "description": "RESEARCH Agent",
            "skills": [{"name": "Research", "description": "Deep Research", "id": "s1"}],
        }
        score = _match_skills_to_goal("research", info)
        assert score > 0.0


# ---------------------------------------------------------------------------
# A2A List tool handler
# ---------------------------------------------------------------------------

class TestA2AList:
    def test_empty_registry(self):
        """Empty registry returns total=0."""
        from tools.a2a_tool import a2a_list
        with patch("tools.a2a_tool._build_agent_registry", return_value=[]):
            result = json.loads(a2a_list({}))
            assert result["total"] == 0
            assert result["agents"] == []

    def test_populated_registry(self):
        """Registry with agents returns them all."""
        from tools.a2a_tool import a2a_list
        agents = [
            {"name": "a1", "url": "http://a1", "source": "config",
             "status": "configured", "skills": [], "description": ""},
            {"name": "a2", "url": "http://a2", "source": "discovered",
             "status": "discovered", "skills": [], "description": ""},
        ]
        with patch("tools.a2a_tool._build_agent_registry", return_value=agents):
            result = json.loads(a2a_list({}))
            assert result["total"] == 2
            assert len(result["agents"]) == 2

    def test_error_handling(self):
        """Exceptions return error JSON."""
        from tools.a2a_tool import a2a_list
        with patch("tools.a2a_tool._build_agent_registry", side_effect=RuntimeError("boom")):
            result = json.loads(a2a_list({}))
            assert "error" in result


# ---------------------------------------------------------------------------
# A2A Orchestrate tool handler
# ---------------------------------------------------------------------------

class TestA2AOrchestrate:
    def test_missing_goal(self):
        """Missing goal returns error."""
        from tools.a2a_tool import a2a_orchestrate
        result = json.loads(a2a_orchestrate({}))
        assert "error" in result
        assert "goal" in result["error"].lower()

    def test_empty_goal(self):
        """Empty goal string returns error."""
        from tools.a2a_tool import a2a_orchestrate
        result = json.loads(a2a_orchestrate({"goal": "  "}))
        assert "error" in result

    def test_invalid_mode(self):
        """Invalid mode returns error."""
        from tools.a2a_tool import a2a_orchestrate
        result = json.loads(a2a_orchestrate({"goal": "test", "mode": "invalid"}))
        assert "error" in result
        assert "invalid" in result["error"].lower()

    @patch("tools.a2a_tool._run_on_loop")
    @patch("tools.a2a_tool._resolve_agent_url")
    def test_explicit_agents_all_mode(self, mock_resolve, mock_run):
        """Explicit agents with mode=all calls orchestrate correctly."""
        from tools.a2a_tool import a2a_orchestrate
        mock_resolve.return_value = ("http://agent1:8080", {})
        mock_run.return_value = {
            "mode": "all",
            "agents_called": 1,
            "results": [{"agent": "agent1", "url": "http://agent1:8080",
                         "status": "success", "response": "done", "duration_ms": 100}],
        }
        result = json.loads(a2a_orchestrate({
            "goal": "Research AI",
            "agents": ["agent1"],
            "mode": "all",
        }))
        assert result["mode"] == "all"
        assert result["agents_called"] == 1
        assert result["results"][0]["status"] == "success"

    @patch("tools.a2a_tool._run_on_loop")
    @patch("tools.a2a_tool._resolve_agent_url")
    def test_first_mode(self, mock_resolve, mock_run):
        """Mode=first returns first success."""
        from tools.a2a_tool import a2a_orchestrate
        mock_resolve.return_value = ("http://a:8080", {})
        mock_run.return_value = {
            "mode": "first",
            "agents_called": 1,
            "results": [{"agent": "a", "url": "http://a:8080",
                         "status": "success", "response": "fast", "duration_ms": 50}],
        }
        result = json.loads(a2a_orchestrate({
            "goal": "Quick task",
            "agents": ["a"],
            "mode": "first",
        }))
        assert result["mode"] == "first"

    @patch("tools.a2a_tool._run_on_loop")
    @patch("tools.a2a_tool._resolve_agent_url")
    def test_best_mode(self, mock_resolve, mock_run):
        """Mode=best is accepted (alias for all)."""
        from tools.a2a_tool import a2a_orchestrate
        mock_resolve.return_value = ("http://a:8080", {})
        mock_run.return_value = {
            "mode": "best",
            "agents_called": 1,
            "results": [],
        }
        result = json.loads(a2a_orchestrate({
            "goal": "task",
            "agents": ["a"],
            "mode": "best",
        }))
        assert result["mode"] == "best"

    @patch("tools.a2a_tool._run_on_loop")
    @patch("tools.a2a_tool._auto_select_agents")
    def test_auto_select_agents(self, mock_auto, mock_run):
        """No explicit agents triggers auto-select."""
        from tools.a2a_tool import a2a_orchestrate
        mock_auto.return_value = [("http://auto:8080", {"_name": "auto"})]
        mock_run.return_value = {
            "mode": "all",
            "agents_called": 1,
            "results": [{"agent": "auto", "url": "http://auto:8080",
                         "status": "success", "response": "auto-done", "duration_ms": 200}],
        }
        result = json.loads(a2a_orchestrate({"goal": "research AI"}))
        mock_auto.assert_called_once_with("research AI")
        assert result["agents_called"] == 1

    @patch("tools.a2a_tool._resolve_agent_url")
    def test_resolve_failure(self, mock_resolve):
        """Resolve failure returns error JSON."""
        from tools.a2a_tool import a2a_orchestrate
        mock_resolve.side_effect = ValueError("Unknown agent 'bad'")
        result = json.loads(a2a_orchestrate({
            "goal": "task",
            "agents": ["bad"],
        }))
        assert "error" in result

    @patch("tools.a2a_tool._auto_select_agents")
    def test_empty_auto_select(self, mock_auto):
        """Empty auto-select raises and returns error."""
        from tools.a2a_tool import a2a_orchestrate
        mock_auto.side_effect = ValueError("No agents in registry")
        result = json.loads(a2a_orchestrate({"goal": "task"}))
        assert "error" in result


# ---------------------------------------------------------------------------
# Phase 3 Registration
# ---------------------------------------------------------------------------

class TestOrchestrateRegistration:
    def test_list_tool_registered(self):
        """a2a_list is registered in the registry."""
        from tools.registry import registry
        import tools.a2a_tool  # noqa: F401
        assert "a2a_list" in registry.get_all_tool_names()

    def test_orchestrate_tool_registered(self):
        """a2a_orchestrate is registered in the registry."""
        from tools.registry import registry
        import tools.a2a_tool  # noqa: F401
        assert "a2a_orchestrate" in registry.get_all_tool_names()

    def test_toolset_includes_new_tools(self):
        """a2a toolset includes all 4 tools."""
        from toolsets import resolve_toolset
        tools = resolve_toolset("a2a")
        assert "a2a_discover" in tools
        assert "a2a_call" in tools
        assert "a2a_list" in tools
        assert "a2a_orchestrate" in tools

    def test_schemas_valid(self):
        """New tool schemas have required fields."""
        from tools.a2a_tool import A2A_LIST_SCHEMA, A2A_ORCHESTRATE_SCHEMA

        assert A2A_LIST_SCHEMA["name"] == "a2a_list"
        assert "parameters" in A2A_LIST_SCHEMA

        assert A2A_ORCHESTRATE_SCHEMA["name"] == "a2a_orchestrate"
        assert "goal" in A2A_ORCHESTRATE_SCHEMA["parameters"]["properties"]
        assert "agents" in A2A_ORCHESTRATE_SCHEMA["parameters"]["properties"]
        assert "mode" in A2A_ORCHESTRATE_SCHEMA["parameters"]["properties"]
        assert "goal" in A2A_ORCHESTRATE_SCHEMA["parameters"]["required"]
