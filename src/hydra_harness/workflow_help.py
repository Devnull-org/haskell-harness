"""Terminal guide for choosing and resuming each workflow phase."""

WORKFLOW_HELP = """Haskell Harness workflow

Choose how to launch
  From the harness clone (the directory containing its flake.nix):
    nix run . -- help
  From any directory, without cloning the harness:
    nix run github:Devnull-org/langgraph-harness -- help
  Install the command with Nix (once, from any directory):
    nix profile add --refresh github:Devnull-org/langgraph-harness
  Then use it directly in your terminals:
    haskell-harness help

  The default package includes all built-in backend CLIs. No backend flag is needed.
  Task commands check installed backends and saved logins, then make a small
  generation request without project data. One usable backend is selected;
  several usable backends produce a numbered choice in this terminal.
  A usage-limited backend is excluded even when it is signed in.
  Checks use provider allowance and take at most 60 seconds per generation call.
  Codex uses your saved ChatGPT login. Each provider has its own usage allowance.
  --backend NAME or a named package such as #codex forces that backend instead.
  --backend auto restores automatic selection if a user default forces a backend.
  Scripts and --json return exit code 2 with available choices when selection
  needs your input. status, help, backends, and context do not contact models.
  If Nix is older and does not recognize profile add, use profile install instead.
  Without Nix, from the harness clone with uv and Git installed:
    uv sync --locked --no-dev
    source .venv/bin/activate
  This makes haskell-harness available in that terminal.

  Below, replace haskell-harness with either Nix prefix above, ending in --.
  Replace /path/to/project with your local target Git checkout. --repo selects
  that project; it does not tell Nix where to find the harness flake.
  my-feature is your task name. Reuse it and the same --repo to continue later.
  help displays this guide without logging in, running agents, or creating tasks.

1. Check your tools and login
    haskell-harness --repo /path/to/project doctor
  If needed, sign in through your selected agent CLI with your existing account.
  doctor checks tools and login; it does not build or test the project.

2. Save shared project knowledge (optional)
    haskell-harness --repo /path/to/project context refresh --profile hydra
  The hydra profile imports protocol and state-machine sources from committed
  HEAD. For another project, use --source docs/architecture.md with a real tracked
  path; repeat --source for more files. context show displays the saved knowledge.

3. Choose a base and describe the work
    haskell-harness --repo /path/to/project start my-feature "Describe the change"
  You are asked for a base branch or commit. Enter accepts the displayed current
  branch (HEAD if detached). To select it without a prompt:
    haskell-harness --repo /path/to/project start my-feature "Describe the change" --base main
  Replace main with your branch, an already fetched origin/main, tag, or commit.
  The exact commit is saved. Each task gets its own branch and worktree before
  the interview, excluding staged, unstaged, and untracked local changes.
  The source checkout stays as it is. The harness does not fetch remote branches.
  Omit the description to write it in the terminal editor instead. Alt+Enter or
  Ctrl+J adds a newline; Enter sends the whole message. Pasted paragraphs stay
  together. Escape followed by Enter also adds a newline.
  Scripts and --json require an explicit --base and request.

  To explore code and compare options instead of specifying a change:
    haskell-harness --repo /path/to/project explore my-question "Explore the alternatives"
  This uses the same base prompt and multiline request editor. The explorer reads
  the code and delivers an analysis, with references and trade-offs. Type follow-up
  questions, quit to resume later, or done on its own to finish the discussion.
  The latest analysis is saved in reports/exploration.md and exploration.json.
  No tests, builds, source edits, or commits are made by this phase.
  If you already approved a specification asking for an exploration:
    haskell-harness --repo /path/to/project run my-feature explore
  It uses that task's scope and clarifications; no restart or repeated issue is needed.
  After done, stop there or use run TASK explore to reopen the exploration.
  To implement an approach later, explicitly run TASK interview to agree on a
  new specification. Research is background; tests still require your review.

4. Discuss and approve the specification
  Type answers directly in the same terminal and press Enter; no answer prefix
  is needed. At the specification gate, plain text asks a question. Use revise
  TEXT to request changes, or approve on its own to accept the specification.
  Answers, questions, and revisions use the same multiline editor. Type revise
  alone to open a separate instructions prompt. An approve line within a
  multiline message never approves a gate. Ctrl+C discards the unsent draft;
  already submitted messages remain saved for resume.
  Approval saves it and stops. Tests do not start automatically.

5. Write failing tests and review them yourself
    haskell-harness --repo /path/to/project run my-feature tests
  Inspect the diff and validation evidence in the displayed worktree and reports.
  Use question TEXT or revise TEXT to discuss the tests; approve accepts them.
  Expected assertion failures are allowed here. Compilation, lint, and required
  Nix checks still apply. For Hydra, project tests run through Cabal.
  Approval commits the tests and stops before implementation.

6. Implement, then refactor
    haskell-harness --repo /path/to/project run my-feature implement
    haskell-harness --repo /path/to/project run my-feature refactor
  Run each command when the preceding phase is complete. The Haskell implementer
  must pass the approved tests; the refactorer then improves the code and comments.
  Each patch is validated before committing. Proposed test changes need approval.

7. Run both reviews, in either order
    haskell-harness --repo /path/to/project run my-feature review-haskell
    haskell-harness --repo /path/to/project run my-feature review-security
  These cover Haskell correctness/performance and Cardano security/liveness.
  Discuss findings with question TEXT; select fix ID or dismiss ID with a reason.
  status lists which phase is available to address a fix. Behavior changes return
  to tests and approval; refactoring a fix makes both reviews required again.

8. Retain context and prepare the local handoff
    haskell-harness --repo /path/to/project run my-feature walkthrough
  Optional code questions help you understand the changes. Type an answer or skip.
  Add --walkthrough to start to require this phase before handoff.
    haskell-harness --repo /path/to/project run my-feature handoff
  Handoff writes local reports and a PR description. You inspect the patches and
  decide when to push and open a PR; the harness does neither automatically.

Pause and continue whenever needed
  Type quit at a conversation prompt to leave the saved task.
    haskell-harness --repo /path/to/project status my-feature
    haskell-harness --repo /path/to/project resume my-feature
  status shows the base commit, worktree, reports, and available next phases.
  resume retries a saved backend failure once with the newly selected backend.
  It returns to pending human questions and approvals without answering them.
  Use run
  to start a different phase; completed phases never advance automatically.
  Use the same --state-dir if you supplied one. No new --base is needed on resume.

Abandon an idea permanently
    haskell-harness --repo /path/to/project abandon my-feature
  Or type abandon at a conversation prompt. The harness lists the task files,
  worktree, and local branch, then asks you to type the task name to delete them.
  Enter cancels. Deletion includes the conversation, reports, task commits, and
  uncommitted task files. You cannot resume it, but can reuse the name.
  The source checkout, other tasks, and shared project knowledge are kept.
  No backend or login is needed. Quit other conversations for that task first.
  Add --yes to confirm without a prompt; scripts and --json require it.
  If status shows abandoning after an error, repeat abandon to finish deletion.

Switch backends on a saved task
  Quit the old process, then select the backend when resuming:
    haskell-harness --backend codex --repo /path/to/project resume my-feature
  resume retries backend_blocked with Codex; older agent_error states need retry.
  Your answers, approvals, base commit, and worktree are preserved.
  Or use the GitHub Nix prefix above with #codex to supply its CLI too.

Start over while preserving the old task
    haskell-harness --repo /path/to/project restart my-feature
  This repeats the original starting phase (interview or exploration), named
  my-feature-restart-1 (or the next free number). It reuses the original request
  and pinned commit, and creates a new
  worktree. The previous task, its files, and its commits stay available.
  Use the new task name shown in the output for all subsequent commands.
  Add --as my-feature-v2 to choose a name, --base main to select another base, or
  a quoted replacement description after the original task name.

While waiting for an agent
  Press Ctrl+C to stop generation and return to an agent_paused prompt.
  Type corrected instructions, or revise to edit the previous message.
  retry repeats it unchanged. quit saves the pause; resume returns to it without
  automatically resending the cancelled request. Partial responses are discarded.
  At the message editor, Ctrl+C still leaves without submitting the unsent draft.
  A spinner shows the agent role and elapsed time, then clears before your prompt.
  With --json, redirected stderr, or TERM=dumb, progress is printed every 15 seconds
  instead. The indicator shows waiting time, not the amount of completed work.
  Invalid JSON/schema responses get one automatic correction attempt. The same
  backend receives the error and original response; your answers remain saved.
  Large invalid replies are saved without correction. Validation and approval
  gates still apply.
  Unresolved responses and provider failures pause at backend_blocked with a
  reason and diagnostics path. Quota, login, rate-limit, and timeout failures
  do not trigger a retry loop or a silent provider switch. retry repeats the call
  with this session's backend. Quit and resume to check and choose backends again.
  --json and scripts return nonzero for blocked tasks.
  Failed calls are saved under backend-responses, with a separate attempt-2
  directory for a failed correction. restart is not needed to correct a reply.

Command and option reference
    haskell-harness --help
    haskell-harness start --help
    haskell-harness explore --help
    haskell-harness run --help
    haskell-harness restart --help
  Global options (--repo, --backend, --json) go before the command. start options
  (--base, --walkthrough) go after start. explore accepts --base after the command.
  Use --json with respond for scripted gates. Exploration accepts question --text
  TEXT or done; finishing it needs no approval token and starts no further work.
"""
