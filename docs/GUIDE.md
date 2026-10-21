# Haskell Harness user guide

The website is the interface for conversations, approvals, phases, saved work,
backend selection, and project context. Open **Workflow guide** in the sidebar
for an in-app walkthrough.

## Start the local server

From any directory, with Nix and Git installed:

```sh
nix run --refresh github:Devnull-org/haskell-harness -- --repo "/path/to/project"
```

Replace the path with the root of your existing Git checkout. Open the printed URL,
usually `http://127.0.0.1:8765`. Keep the server process running. Closing the browser
leaves active work running. Ctrl+C in the server's terminal stops the server and
cancels active work while keeping submitted messages and completed steps saved.

If an assistant started the server for you, it can be running in the assistant's
background session even when your terminal is idle. The browser connects to that
local process; it cannot operate on the repository without the server.

There is no terminal workflow client or separately installed `haskell-harness`
command. Nix supplies a small website launcher. Task names and responses belong
in the page, with no `serve`, `start`, or `resume` subcommand in the launch line.

### From a clone

```sh
git clone git@github.com:Devnull-org/haskell-harness.git
cd haskell-harness
nix run . -- --repo "/path/to/project"
```

Nix resolves `.` before reading `--repo`. It must point at the harness clone's
flake. From elsewhere, use the GitHub reference or an absolute path to the clone.
A `could not find a flake.nix file` error occurs before the harness starts.

### Without Nix

Install Git and [uv](https://docs.astral.sh/uv/getting-started/installation/), clone
the repository, and run:

```sh
uv run --locked --no-dev python -m haskell_harness --repo "/path/to/project"
```

uv installs the pinned Python dependencies and starts the same local website.
Install your chosen model provider's CLI separately and sign in using its normal
login flow. Nix is optional for exploration, interviews, and reviews; code validation needs
the target project's Nix environment.

### Server options

All options go after `--` with `nix run`. There are no workflow subcommands.

| Option | Purpose |
| --- | --- |
| `--repo PATH` | Required target Git checkout; no project path is assumed. |
| `--port PORT` | Loopback port, default 8765; 0 chooses a free port. |
| `--state-dir PATH` | Saved tasks, logs, and worktrees; outside the checkout. Default: `$XDG_STATE_HOME/haskell-harness` or `~/.local/state/haskell-harness`. |
| `--config FILE` | User-owned backend and validation configuration. |
| `--nix-flake PATH` | Dependency flake override; otherwise use each task's worktree. |
| `--backend NAME` | Initial backend choice; also selectable on the page. |
| `--model ID` | Model override for all agents. |
| `--agent-timeout SECONDS` | Override the backend request timeout. |
| `--check-timeout SECONDS` | Validation command timeout, default 3600. |
| `--unsigned-commits` | Disable signing for harness commits only. |
| `--help` | Describe server startup options. |

Existing tasks keep their worktrees and checkpoints. When upgrading from a release
with a different state directory, pass `--state-dir /path/to/existing/state-root`
and any existing `--config` override. Keep the same target checkout path. Do not
move registered worktrees. Restart the server to load an updated version.

## Choose a starting point

**Explore** reads code and delivers findings, options, references, and follow-up
answers. It does not start coding. **Finish exploration** saves the result; you
can stop there or start a requirements interview later.

**Plan a feature or fix** starts an interview. Answer questions and revise the
specification until you are satisfied, then explicitly approve it.

Choose a unique task name and a base branch, tag, or commit before starting.
Each task gets its own worktree from that exact commit. Uncommitted changes in
your original checkout stay there. Local and remote-tracking branch names must
already exist in your checkout; GitHub PR URLs are fetched as described below.

### Review an existing change

Choose **Review a PR or branch**. Enter a branch, commit, or GitHub PR URL and
choose **Haskell**, **Security**, or both reviewers. Add a focus if you want to
direct their attention. No requirements interview or code-writing phase is needed.

For a local branch or commit, select its comparison base (usually the target
branch). The review uses the diff from their merge base to the selected head,
excluding unrelated changes made later on the base branch. Both revisions are
pinned when the task starts; the review worktree contains the head revision.

A GitHub PR URL uses the PR's target branch and includes its title and description.
It must belong to a GitHub remote configured in the selected project. The harness
reads metadata through `gh` and fetches the base and PR head without changing
branches or working files. The Nix launcher includes `gh`; without Nix, install
the GitHub CLI separately. Use its existing login (`gh auth login` if needed),
and ensure Git can read the project's remote. URL support is currently for
`github.com`; for other hosts, fetch the branch yourself and use its local ref.

Read the findings, ask follow-up questions, or revise the review's focus.
**Finish review** saves the result; it does not fix code, run builds, post comments,
or approve the PR on GitHub. Reports include `review.md` and `review.json`.
**Start a fresh attempt** reviews the same pinned revision. To review newer commits,
create a new task with the updated branch or PR URL.

### Write tests without implementation

Choose **Write tests**. Select a base, name the Cabal packages (comma-separated),
describe the test scope, and list behaviors to test, one per line. Choose:

- **Failing regression test (TDD)** for a bug or behavior that is not implemented yet.
- **Passing coverage for existing behavior** to cover code that already works.

The test author writes tests, fixtures, and necessary Cabal test wiring. Lint,
strict compilation, Nix checks, and Cabal tests run before your review. Named tests
must appear in fresh JUnit evidence; passing unrelated tests is not enough.
Use **Ask a question**, **Revise instructions**, or **Run checks again**, then
**Approve tests** to commit the validated patch. Approval finishes this task.
There is no implementation phase attached to it. Inspect the worktree and use your
normal Git process to take the commit into your project.

## Continue at your pace

After specification approval, choose **Write failing tests**. Ask questions and
approve the exact tests before choosing **Implement**. Then choose **Refactor**,
**Haskell review**, and **Security review**. Resolve findings before handoff.

The optional **Code walkthrough** asks questions about the finished code. You can
require it when starting a task, or choose it later. **Prepare local handoff**
writes reports and a PR description; it does not push or open a PR.

Enter adds a newline. **Send** or Ctrl/Command+Enter sends the message. Use **Stop
model** to interrupt generation, **Edit previous message** to revise it, or
**Retry unchanged** to repeat it. Only an approval button approves work.

Saved tasks appear in the sidebar. **Resume** returns to an unfinished phase or
its pending gate. **Start a fresh attempt** preserves the old task and creates a
new worktree from its original pinned base. **Abandon task** permanently deletes
the selected task's conversation, reports, branch, and worktree after you type
its name to confirm.

## Project context

Open **Project settings & context**. Enter committed source/document paths, one
per line, and choose **Save snapshot**. Use architecture notes, invariants, state
machine definitions, or other reference material. No model is involved.

The harness snapshots committed HEAD, excluding working-tree edits. **Read
snapshot** shows the saved files. New tasks pin the latest snapshot; existing
tasks retain theirs. **Use for selected task** changes a task's snapshot only
between completed phases. A source mismatch is reported to agents instead of
silently presenting stale source as current.

## Backends and recovery

Automatic selection checks available executables, existing login probes, and a
small generation request. It sends no project data during discovery. If several
backends work, choose one on the page. Authentication and usage allowances belong
to each provider; being logged in does not establish remaining quota.

Project settings lets you select another backend. **Check backends and resume**
checks availability again and continues the selected task. Provider failures
pause work and retain diagnostics; they never bypass approvals or trigger an
automatic provider fallback. See [backend configuration](../BACKENDS.md).

## Validation and local reports

[Validation and commits](VALIDATION.md) covers Cabal, strict GHC builds, HLint,
Nix checks, JUnit reports, expected failures, and custom commands.

The displayed report directory contains the current specification, exploration,
validation logs, patch series, independent reviews, finding dispositions, and
handoff draft. Review these and use your team's normal publishing process.
Agent review is advisory and does not establish that code is free of defects.

The server binds only to `127.0.0.1`. Model processes use your configured provider
and its access. The harness checks proposed changes and unexpected worktree edits;
this is not an operating-system sandbox. Use trusted backend configuration.
