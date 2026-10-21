# Lambda Harness user guide

The website is the interface for conversations, approvals, phases, saved work,
backend selection, and project memory. Open **Workflow guide** in the sidebar
for an in-app walkthrough.

## Start the local server

From any directory, with Nix and Git installed:

```sh
nix run --refresh github:Devnull-org/lambda-harness -- --repo "/path/to/project"
```

Replace the path with the root of your existing Git checkout. Open the printed URL,
usually `http://127.0.0.1:8765`. Keep the server process running. Closing the browser
leaves active work running. Ctrl+C in the server's terminal stops the server and
cancels active work while keeping submitted messages and completed steps saved.

If an assistant started the server for you, it can be running in the assistant's
background session even when your terminal is idle. The browser connects to that
local process; it cannot operate on the repository without the server.

There is no terminal workflow client or separately installed `lambda-harness`
command. Nix supplies a small website launcher. Task names and responses belong
in the page, with no `serve`, `start`, or `resume` subcommand in the launch line.

### From a clone

```sh
git clone git@github.com:Devnull-org/lambda-harness.git
cd lambda-harness
nix run . -- --repo "/path/to/project"
```

Nix resolves `.` before reading `--repo`. It must point at the harness clone's
flake. From elsewhere, use the GitHub reference or an absolute path to the clone.
A `could not find a flake.nix file` error occurs before the harness starts.

### Without Nix

Install Git and [uv](https://docs.astral.sh/uv/getting-started/installation/), clone
the repository, and run:

```sh
uv run --locked --no-dev python -m lambda_harness --repo "/path/to/project"
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
| `--agents-repo PATH` | Git checkout containing `agents/*.json`; overrides the saved catalog selection. Source launches detect the harness checkout. Installed packages otherwise show the bundled catalog. |
| `--port PORT` | Loopback port, default 8765; 0 chooses a free port. |
| `--state-dir PATH` | Saved tasks, logs, and worktrees; outside the checkout. Default: `$XDG_STATE_HOME/lambda-harness` or `~/.local/state/lambda-harness`. |
| `--config FILE` | User-owned backend and validation configuration. |
| `--nix-flake PATH` | Dependency flake override; otherwise use each task's worktree. |
| `--backend NAME` | Initial backend choice; also selectable on the page. |
| `--model ID` | Model override for all agents. |
| `--agent-timeout SECONDS` | Override the backend request timeout. |
| `--check-timeout SECONDS` | Validation command timeout, default 3600. |
| `--unsigned-commits` | Disable signing for harness commits only. |
| `--help` | Describe server startup options. |

Existing tasks keep their worktrees and checkpoints. The previous product's default
state directory is detected for this checkout when it has no state in the new
directory. Nothing is moved or merged. The previous default configuration and
browser theme are also reused when no new setting exists.

For a custom or older state location, keep passing
`--state-dir /path/to/existing/state-root` and any existing `--config` override.
Keep the same target checkout path; do not move registered worktrees. Restart the
server to load an updated version.

## Your agent catalog

Open **Agents** in the sidebar, or visit `/agents`. Browse portraits, roles,
languages, and expertise. **View agent** shows the full instructions. Search by
name, language, or expertise, and filter by role.

**Add agent** starts a guided chat. Describe the help you need; you do not need to
know the language's best practices. The builder proposes suitable expertise,
explains tradeoffs, and asks focused questions. Ask for refinements, including a
different avatar, until the definition fits. **Save to repository** writes the
reviewed definition. **Edit** opens a new conversation about an existing agent;
its saved definition remains unchanged until you save the revision. You can stop
model generation and revise instructions, just as in other conversations.

Definitions live in `agents/*.json` in a Git checkout, including the avatar name.
The harness repository includes Haskell specialists, language-neutral roles, and
a Rust reviewer. To make changes when launching directly from GitHub, clone it:

```sh
git clone git@github.com:Devnull-org/lambda-harness.git
```

On the Agents page, expand **Agent repository** and enter that clone's path.
You can also select another team's Git checkout containing an `agents/` directory.
An empty catalog is supported. When running from the harness source checkout,
it is detected automatically. The bundled catalog in an installed/Nix package
is read-only until you select a writable checkout.

Saving creates an ordinary working-tree change. Review the diff, commit it, and
push it or open a PR using your team's usual process. Teammates pull the repository
and refresh the catalog to receive updates. The website does not commit or push
agent definitions automatically. Git provides their history. Draft conversations
and project selections are local; definitions are not stored in a local database.

Use **Agents for this project**, or **Use for this project** on a profile, to select
an agent for a workflow role. New tasks pin complete definitions, including their
version and avatar. Editing a file or pulling an updated catalog does not change
existing tasks. A deleted or incompatible selected definition blocks new tasks
until you update the assignment. Role permissions, backend choice, approval gates,
and validation remain controlled by the harness.

The coding toolchain currently supports Haskell and Nix. Rust and other languages
can already have chat, exploration, and review specialists. You can draft coding
agents for other languages, but they cannot be assigned to test-writing,
implementation, or refactoring until a matching toolchain is supported.

Agent conversations appear separately on the Agents page, so they do not clutter
your project task list. **Save to repository** does not automatically assign a
new agent. Assignments are personal to this project; sharing definitions does not
force teammates to use them.

## Choose a starting point

**General chat** is an ongoing conversation for questions, explanations, or brainstorming.
It uses the selected repository revision and saved project context when relevant,
without an interview, specification, or approval step. Send follow-up messages,
leave, and return to the saved chat whenever you like. **Stop model** and revised
instructions work here too. Chat does not edit files or start a coding workflow.
Its transcript is also saved as `chat.md` in the report directory.

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
choose **Code**, **Security**, or both reviewers. The code reviewer uses the
agent selected for this project, with a Haskell reviewer provided by default. Add a focus if you want to
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

## Project memory

Open **Project memory** in the sidebar to see the current notes and saved source
files. Add or edit architecture notes, invariants, decisions, and terminology,
then choose **Save memory**. Notes support plain text or Markdown and are included
in full (up to 32,000 UTF-8 bytes). No model call or repository edit is involved.

In **General chat**, you can say “Save this to project memory: every job must be
safe to retry,” or ask to remember a clear point from the conversation. The agent
returns a note for the harness to append to current memory. The conversation shows
**Saved to project memory**, the exact text, and a link to its saved version only
after the write succeeds. That chat uses the new version immediately; other tasks
keep theirs. Ordinary discussion does not request a memory write. If “this” is
ambiguous, the agent asks what to save. Editing or deleting existing notes still
uses the editor. A failed save shows its reason and keeps the proposed note.

Reference paths are relative to the selected repository, one per line. Adding a
path captures **all listed files** from committed HEAD. Editing notes or removing
a path keeps the retained files' saved contents. **Refresh sources & save** reads
them again from committed HEAD; uncommitted edits are excluded. Limits are 24
tracked regular text files, 1 MB each and 3 MB total. Expand **Saved source contents**
to read the full copies. To clear current memory, empty both fields and save.

**What agents receive** previews the saved notes, source excerpts, and stale-source
flags. Each matching source contributes up to 4,000 characters and 100 symbols;
changed or missing sources have no excerpt. The project preview compares against
the main checkout. **Preview selected task's memory** uses that task's pinned
version and actual worktree. Repository instructions and conversation history
are separate from this project memory.

Every edit creates an immutable version. **Version history → View version** reads
an older one; **Make this current** restores it for future tasks without deleting
history. The editor preserves drafts across polling and panel changes, and
rejects a save if another session has changed current memory. Copy any edits you
want to keep before using **Discard draft & load current** to resolve that conflict.
Unsaved drafts remain only in the current browser tab and are lost on page reload.

New tasks pin the current version. Existing tasks keep their version until you
choose **Use viewed version for selected task**. This is allowed between completed
phases or between general chat messages, never during model work or a pending
review. Saving or restoring project memory does not change existing task history.
Memory is local to this project's harness state directory; Git does not sync it.

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
