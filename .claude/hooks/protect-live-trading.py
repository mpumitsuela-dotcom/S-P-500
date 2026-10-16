#!/usr/bin/env python3
"""PreToolUse hook: make Claude ask the owner before touching secrets or live trading.

The owner's rules (.claude/skills/trading-agent-ops/SKILL.md) say live trading
and the API keys are the owner's decisions. This hook enforces that instead of
relying on Claude remembering it:

- editing or overwriting .env (the file with the real API keys; .env.example is fine)
- any change that adds one of the three live-trading gates: the live Alpaca URL,
  LIVE_TRADING_CONFIRMED=YES_I_UNDERSTAND, or allow_live=True

Tests and Markdown docs are exempt, because the tests exercise the gates on
purpose and the README explains them. Anything else asks the owner first.
"""
import json
import os
import re
import sys

LIVE_MARKERS = [
    (re.compile(r"https?://api\.alpaca\.markets"), "the live Alpaca URL"),
    (re.compile(r"YES_I_UNDERSTAND"), "the live-trading confirmation value"),
    (re.compile(r"allow_live\s*=\s*True"), "allow_live=True"),
]
ENV_FILE = re.compile(r"(^|/)\.env(\.[A-Za-z0-9_-]+)?$")
# Shell commands that write to a file named .env (not .env.example)
ENV_WRITE = re.compile(r"(>>?|\btee\b|\bsed\s+-i|\bcp\b|\bmv\b|\brm\b)[^|;&]*(^|[\s/'\"])\.env(?!\.example)\b")


def is_secret_env(path):
    return bool(ENV_FILE.search(path)) and not path.endswith(".env.example")


def is_exempt(path):
    p = path.replace("\\", "/")
    return p.endswith(".md") or "/tests/" in f"/{p}" or "/.claude/hooks/" in f"/{p}"


def new_text(tool, inp):
    if tool == "Write":
        return inp.get("content", "")
    if tool == "Edit":
        return inp.get("new_string", "")
    if tool == "MultiEdit":
        return "\n".join(e.get("new_string", "") for e in inp.get("edits", []))
    if tool == "NotebookEdit":
        return inp.get("new_source", "")
    return ""


def ask(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": reason + " This is the owner's decision (see the trading-agent-ops rules), so ask them first.",
    }}))
    sys.exit(0)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}

    if tool == "Bash":
        cmd = inp.get("command", "")
        if ENV_WRITE.search(cmd):
            ask("This command changes .env, which holds the real API keys.")
        for rx, label in LIVE_MARKERS:
            if rx.search(cmd):
                ask(f"This command uses {label}, one of the three live-trading gates.")
        return

    path = inp.get("file_path") or inp.get("notebook_path") or ""
    rel = os.path.relpath(path, os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())) if path else ""
    if is_secret_env(rel):
        ask(f"{rel} holds the real API keys.")
    if is_exempt(rel):
        return
    text = new_text(tool, inp)
    for rx, label in LIVE_MARKERS:
        if rx.search(text):
            ask(f"This change to {rel} adds {label}, one of the three live-trading gates.")


if __name__ == "__main__":
    main()
