"""Recognise which AI agent (if any) a process command line belongs to."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Only the interpreter and the script/binary are inspected. Looking at the whole
# argv would misclassify e.g. `vim ~/.claude/settings.json` as Claude Code.
ARGV_POSITIONS_TO_INSPECT = 2


@dataclass(frozen=True)
class AgentSignature:
    name: str
    # Exact executable basenames, e.g. "claude" for /home/u/.local/bin/claude.
    basenames: frozenset[str]
    # Substrings matched against the inspected argv entries (lowercased).
    path_fragments: tuple[str, ...]


AGENT_SIGNATURES: tuple[AgentSignature, ...] = (
    AgentSignature("Claude Code", frozenset({"claude"}), ("@anthropic-ai/claude-code", "/claude-code/")),
    AgentSignature("Codex", frozenset({"codex"}), ("@openai/codex", "/.codex/packages/")),
    AgentSignature("Hermes", frozenset({"hermes"}), ("hermes_cli", "/.hermes/hermes-agent/")),
    AgentSignature("OpenClaw", frozenset({"openclaw"}), ("/openclaw/", "openclaw.mjs")),
    AgentSignature("OpenCode", frozenset({"opencode"}), ("/.opencode/", "opencode-ai")),
    AgentSignature("Antigravity", frozenset({"agy", "antigravity"}), ("/antigravity-cli/",)),
    AgentSignature("Gemini CLI", frozenset({"gemini"}), ("@google/gemini-cli",)),
    AgentSignature("Z Code", frozenset({"zcode"}), ("/.zcode/",)),
    AgentSignature("Paperclip", frozenset({"paperclip"}), ("/.paperclip/", "paperclipai")),
    AgentSignature("Ollama", frozenset({"ollama"}), ()),
)

AGENT_NAMES: tuple[str, ...] = tuple(sig.name for sig in AGENT_SIGNATURES)


def _signature_matches(signature: AgentSignature, inspected_argv: list[str]) -> bool:
    for arg in inspected_argv:
        if os.path.basename(arg) in signature.basenames:
            return True
        lowered = arg.lower()
        if any(fragment in lowered for fragment in signature.path_fragments):
            return True
    return False


def classify_agent_cmdline(cmdline: list[str] | tuple[str, ...]) -> str | None:
    """Return the agent name for a process command line, or None if it is not an agent."""
    if not cmdline:
        return None
    inspected_argv = [arg for arg in cmdline[:ARGV_POSITIONS_TO_INSPECT] if arg]
    for signature in AGENT_SIGNATURES:
        if _signature_matches(signature, inspected_argv):
            return signature.name
    return None
