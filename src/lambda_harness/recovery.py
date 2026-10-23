"""Recovery choices derived from the failed operation, independent of agent role."""

import re


def can_recover(state):
    return state.get("stage") in {"agent_error", "backend_blocked", "validation_blocked"}


def commit_failed(state):
    return state.get("stage") == "agent_error" and state.get("retry_node") == "commit"


def recovery_actions(state):
    actions = list(state.get("prompt", {}).get("actions", []))
    if can_recover(state):
        actions.append("question")
    if commit_failed(state):
        actions.append("continue_without_commits")
    return list(dict.fromkeys(actions))


def recovery_prompt(state):
    prompt = dict(state["prompt"])
    prompt["actions"] = recovery_actions(state)
    if commit_failed(state):
        prompt["guidance"] = (
            "The commit step failed. Your files are still in the task worktree. "
            "Fix the cause and Retry, ask for help or a different approach, or choose "
            "Continue without commits. That choice leaves this task's remaining changes "
            "uncommitted while validation and reviews continue."
        )
        error = prompt.get("error", "").lower()
        if "gpg" in error and any(word in error for word in ("tty", "passphrase", "pinentry")):
            match = re.search(r"KEY_CONSIDERED ([A-Fa-f0-9]{40,64})\b", prompt.get("error", ""))
            key = " --local-user " + match[1] if match else ""
            prompt.setdefault(
                "suggested_commands",
                [
                    {
                        "explanation": "Run in a terminal on the harness machine to unlock "
                        "the signing key, then click Retry. This signs a test message, "
                        "not a commit. "
                        "Unlocking is temporary; your GPG agent's cache settings apply.",
                        "command": 'export GPG_TTY="$(tty)"\n'
                        "gpg-connect-agent updatestartuptty /bye\n"
                        f"printf '%s\\n' 'Lambda Harness signing check' | gpg{key} "
                        "--clearsign > /dev/null",
                    }
                ],
            )
    else:
        prompt["guidance"] = (
            prompt.get("guidance", "") + " " + "Ask what failed, request commands to resolve it, "
            "or describe a different approach. Choose a recovery action to continue."
        ).strip()
    return prompt
