"""Write local reports from recorded analysis and workflow evidence."""


def chat_report(state):
    lines = [
        f"# Chat: {state['task_id']}",
        "",
        f"Repository revision: `{state['base']}`",
        "",
        "## You",
        "",
        state["request"],
    ]
    references = []
    for item in state["messages"]:
        if item.get("text"):
            lines += ["", "## You", "", item["text"]]
        elif item.get("role") == "chat":
            lines += ["", "## Assistant", "", item["answer"]]
            references.extend(item["references"])
            if saved := item.get("memory_save"):
                title = (
                    "Saved to project memory" if saved["status"] == "saved" else "Memory not saved"
                )
                lines += ["", f"### {title}", "", saved["note"], ""]
                lines.append(
                    f"Memory version: `{saved['snapshot']}`"
                    if saved["status"] == "saved"
                    else saved["error"]
                )
    if references:
        lines += ["", "## References", "", *[f"- {ref}" for ref in dict.fromkeys(references)]]
    return "\n".join(lines) + "\n"


def exploration_report(state):
    result = state["exploration"]
    lines = [
        f"# Exploration: {state['task_id']}",
        "",
        f"Inspected revision: `{result['revision']}`",
        "",
        result["summary"],
        "",
        result["analysis"],
    ]
    if result["references"]:
        lines += ["", "## References", "", *[f"- {ref}" for ref in result["references"]]]
    if result["open_questions"]:
        lines += ["", "## Open questions", "", *[f"- {q}" for q in result["open_questions"]]]
    return "\n".join(lines) + "\n"


def standalone_review_report(state):
    source = state["review_source"]
    lines = [
        f"# Review: {state['task_id']}",
        "",
        state["request"],
        "",
        f"Base: `{source['base_ref']}` (`{source['base']}`)",
        f"Head: `{source['head_ref']}` (`{source['head']}`)",
        f"Diff starts at merge base: `{source['merge_base']}`",
        "",
        "Read-only inspection. No builds or tests were run, and no comments were published.",
    ]
    for role, review in state["standalone_reviews"].items():
        lines += ["", f"## {role.replace('_', ' ').capitalize()}", "", review["summary"]]
        for finding in review["findings"]:
            lines += [
                "",
                f"### {finding['id']}: {finding['title']}",
                "",
                f"{finding['severity']} · {finding['confidence']} · {finding['location']}",
                "",
                finding["impact"],
                "",
                f"Conditions: {finding['conditions']}",
                f"Sequence: {' → '.join(finding['sequence'])}",
                f"Evidence: {finding['evidence']}",
                f"Recommendation: {finding['recommendation']}",
            ]
    return "\n".join(lines) + "\n"


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
