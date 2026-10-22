from agentdeck.agents import AGENT_NAMES, classify_agent_cmdline


def test_claude_binary_is_classified_as_claude_code() -> None:
    assert classify_agent_cmdline(["/home/costa/.local/bin/claude", "--resume"]) == "Claude Code"


def test_node_running_claude_code_package_is_classified() -> None:
    cmd = ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"]
    assert classify_agent_cmdline(cmd) == "Claude Code"


def test_hermes_python_entrypoint_is_classified() -> None:
    cmd = ["python3", "/home/costa/.hermes/hermes-agent/hermes_cli/main.py", "gateway", "run"]
    assert classify_agent_cmdline(cmd) == "Hermes"


def test_antigravity_cli_is_classified() -> None:
    assert classify_agent_cmdline(["/home/costa/.local/bin/agy"]) == "Antigravity"


def test_editor_opening_agent_config_is_not_an_agent() -> None:
    assert classify_agent_cmdline(["vim", "/home/costa/.claude/settings.json"]) is None


def test_agent_name_deep_in_argv_is_ignored() -> None:
    assert classify_agent_cmdline(["bash", "-c", "claude --version"]) is None


def test_empty_cmdline_returns_none() -> None:
    assert classify_agent_cmdline([]) is None
    assert classify_agent_cmdline(["", ""]) is None


def test_agent_names_are_unique() -> None:
    assert len(AGENT_NAMES) == len(set(AGENT_NAMES))
