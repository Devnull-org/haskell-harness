"""Terminal guide for choosing and resuming each workflow phase."""

WORKFLOW_HELP = """Haskell Harness workflow

Choose how to launch
  From the harness clone (the directory containing its flake.nix):
    nix run .#local -- help
  From any directory, without cloning the harness:
    nix run github:Devnull-org/langgraph-harness#local -- help
  Install the command with Nix (once, from any directory):
    nix profile add --refresh github:Devnull-org/langgraph-harness#local
  Then use it directly in your terminals:
    haskell-harness help

  The profile installation includes the local backend CLI and selects it by
  default. It uses your existing login. No harness clone is needed.
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
  Scripts and --json require an explicit --base and request.

4. Discuss and approve the specification
  Type answers directly in the same terminal and press Enter; no answer prefix
  is needed. At the specification gate, plain text asks a question. Use revise
  TEXT to request changes, or approve on its own to accept the specification.
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
  resume returns to the current question or finishes interrupted work. Use run
  to start a different phase; completed phases never advance automatically.
  Use the same --state-dir if you supplied one. No new --base is needed on resume.

Start over while preserving the old task
    haskell-harness --repo /path/to/project restart my-feature
  This starts a fresh interview named my-feature-restart-1 (or the next free
  number). It reuses the original request and pinned commit, and creates a new
  worktree. The previous task, its files, and its commits stay available.
  Use the new task name shown in the output for all subsequent commands.
  Add --as my-feature-v2 to choose a name, --base main to select another base, or
  a quoted replacement description after the original task name.

While waiting for an agent
  Elapsed time is printed every 15 seconds until the backend returns or times out.
  Invalid JSON/schema responses stop at agent_error and are saved under the task's
  backend-responses directory; the error gives the path. Use retry to repeat that
  step. After updating the harness, quit the old process and resume with the new
  version before retrying. restart begins a fresh task instead.

Command and option reference
    haskell-harness --help
    haskell-harness start --help
    haskell-harness run --help
    haskell-harness restart --help
  Global options (--repo, --backend, --json) go before the command. start options
  (--base, --walkthrough) go after start. Use --json with respond for scripted gates.
"""
