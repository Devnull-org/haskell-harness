"""Write a self-contained local PR draft from recorded workflow evidence."""


def pr_description(state):
    spec = state["specification"]
    lines = [spec["problem"], "", spec["expected_behavior"], "", "Acceptance criteria", ""]
    lines += [f"- {item['id']}: {item['description']}" for item in spec["acceptance_criteria"]]
    if spec["assumptions"]:
        lines += ["", "Assumptions", "", *[f"- {item}" for item in spec["assumptions"]]]
    lines += ["", "Local patch series", ""]
    for patch in state["commits"]:
        lines.append(
            f"- `{patch['revision'][:12]}`: {patch['summary']} ({patch['validation']['status']})"
        )
        if patch["validation"].get("red_exception"):
            lines.append(f"  Developer's red Nix exception: {patch['validation']['red_exception']}")
    lines += ["", "Final validation", ""]
    for name, command in state["validation"]["commands"].items():
        lines.append(f"- {name}: `{' '.join(command['argv'])}`; exit {command['exit_code']}")
    lines += ["", f"Reviewed revision: `{state['review_revision']}`", "", "Review results", ""]
    for role in ("haskell_reviewer", "security_reviewer"):
        lines += [f"{role.replace('_', ' ').capitalize()}: {state[role]['summary']}", ""]
        for finding in state[role]["findings"]:
            lines += [
                f"- {finding['id']} ({finding['severity']}, {finding['confidence']}): "
                f"{finding['title']} at {finding['location']}",
                f"  Conditions: {finding['conditions']}",
                f"  Sequence: {' → '.join(finding['sequence'])}",
                f"  Impact: {finding['impact']}",
                f"  Evidence: {finding['evidence']}",
                f"  Recommendation: {finding['recommendation']}",
            ]
    for item in state["dispositions"]:
        lines += ["", f"Developer disposition for {item['id']}: {item['reason']}"]
    lines += ["", "Agent reviews are advisory and do not establish absence of security defects."]
    if state.get("walkthrough_answers"):
        lines += ["", "Developer walkthrough", ""]
        for item in state["walkthrough_answers"]:
            lines += [f"- {item['question']}", f"  Developer: {item['answer']}"]
    return "\n".join(lines) + "\n"
