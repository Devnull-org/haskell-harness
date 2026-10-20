# Haskell Harness

An optional local development harness built with LangGraph and selectable model backends. It interviews
the developer, writes failing Haskell tests, waits for the developer to approve
those tests, and then coordinates implementation, refactoring, and reviews.

Run the harness with **uv or Nix** on macOS or Linux. Both use the Python
dependencies pinned in `uv.lock`; the Nix environment also pins its tools in
`flake.lock`. Nix remains available for reproducible development and checks.

Nix is optional for installing and running the harness itself. **Hydra's code
validation still requires Nix** for its dependency flake and Nix checks. Without
Nix you can configure backends, run interviews, and inspect saved tasks. Building,
testing, and committing Hydra patches still requires its full toolchain.

## Quick start

### Before starting a project

With Nix and Git already installed, run this **once per developer machine** from
any directory. Skip the installation if you have already installed the harness:

```sh
nix profile add --refresh github:Devnull-org/langgraph-harness
haskell-harness help
haskell-harness doctor
```

The first command installs the harness and the three built-in agent CLIs. `help`
shows the workflow. `doctor` checks tools and saved logins for the configured
backends. If none are signed in, sign in through an agent CLI using your existing
account, then rerun `doctor`. Older Nix versions may call
`profile add` `profile install`; see [installation details](#install-the-command-with-nix).

**You do not need to specify a backend.** Task commands check every configured
backend's executable and login, then make one small generation request in a
temporary Git directory. The check sends no task description or project files.
A backend that is signed in but reports a usage limit is excluded.

If one backend works, the harness selects it. If several work, it asks you to
choose by number or name in the terminal; it never guesses. If none work, it
reports each failure before starting task execution. Checks use the existing
accounts and consume a small amount of provider usage. Each generation check
has a timeout of at most 60 seconds, bounded by the configured agent timeout.
The selected backend is used for that invocation; later invocations check again.

Codex uses the saved ChatGPT login. Each provider has its own account and usage
allowance. An explicit `--backend NAME`, named Nix package such as `#codex`, or
named `default_backend` in your configuration skips automatic selection.
Use `--backend auto` to override such a choice. See [backend selection](BACKENDS.md).

For **each project**, use an existing local Git checkout. Replace the placeholder
below with its actual path; these commands do not require a harness clone:

```sh
cd "/path/to/project"
git branch --all
haskell-harness --repo . doctor
```

`cd` enters the target project. `git branch --all` lists local and already fetched
remote-tracking branches so you can choose a base. `--repo .` selects this checkout;
the project doctor also checks Nix and its `flake.nix`. To update remote-tracking
branches before choosing a base, optionally run `git fetch origin` (replace `origin`
if your remote has another name). Fetching does not switch your branch or move
uncommitted work.

Optionally save shared project knowledge **before creating the first task**. For
a Hydra checkout, run:

```sh
haskell-harness --repo . context refresh --profile hydra
haskell-harness --repo . context show
```

For another project, use its actual committed documentation paths instead of the
Hydra profile. For example, replace `docs/architecture.md` with a file in that repo:

```sh
haskell-harness --repo . context refresh --source docs/architecture.md
haskell-harness --repo . context show
```

Choose the context example appropriate to your project, or skip it. `refresh`
saves committed sources from the checkout's current HEAD; `show` lets you inspect
them. Repeat `--source` to include more files. New tasks pin the latest saved
snapshot; existing tasks keep theirs. See [Project context](#project-context).

Then start the first feature or bug task:

```sh
haskell-harness --repo . start my-feature "Describe the feature or bug"
```

Replace `my-feature` with a unique task name and the description with your request.
Omit the quoted description to write it in the terminal editor instead. You can
type multiple lines with Alt+Enter or paste paragraphs, then press Enter to send.
The harness asks for the base branch or commit and creates a separate worktree.
You can supply `--base BRANCH_OR_COMMIT` to skip that prompt. Your original checkout
may contain uncommitted changes. The interview stops after specification approval;
later phases must be started explicitly.

Interviews can run for any Git checkout. Code phases currently expect the
[Hydra validation commands](#hydra-validation-and-commits): `just lint`, `just check`,
and `just test PACKAGE_NAME`, with Cabal test results in `test-reports/*.xml`.
Other projects need compatible recipes before running those phases. `doctor`
checks tools, login, and flake presence; it does not verify those recipes or run
builds and tests.

### Run from a harness clone

Clone the harness with `git clone`, then use `cd` to enter its directory:

```sh
git clone git@github.com:Devnull-org/langgraph-harness.git
cd langgraph-harness
```

### With Nix

For a guided explanation of the workflow and its commands:

```sh
nix run . -- help
```

`help` explains setup, base selection, the interview, test approval, each code and
review phase, and how to resume. It needs no target checkout or model login and
does not start work. Use `--help` for a concise command and option reference.

Check installed backend tools and saved logins:

```sh
nix run . -- doctor
```

Here is what each part means:

| Part | Meaning |
| --- | --- |
| `nix run` | Build or fetch the selected application and run it with its dependencies. |
| `.` | Use the flake in the current directory, so run these examples from the harness clone. |
| No `#` selector | Use the default package, which supplies all built-in CLIs and checks backends when starting work. |
| `--` | End Nix's arguments; everything after this goes to the harness. |
| `doctor` | Check required tools and the selected backends' authentication probes. It does not start a task or ask a model to generate code. |

If `doctor` reports **Not logged in**, sign in using the selected agent CLI's
normal login command and your existing account, then rerun `doctor`. The harness
uses the saved CLI login; it does not perform login itself or read credentials.

Without `--repo`, `doctor` checks only the harness and backends. To also check the
target's Nix executable and dependency flake, run this from the harness clone:

```sh
cd "/path/to/langgraph-harness"
nix run . -- --repo "/path/to/project" doctor
```

`/path/to/langgraph-harness` is a placeholder for your harness clone, which contains
the harness's `flake.nix`. Nix resolves `.` before starting the harness.
`--repo` selects the target project after the harness starts; it does not change
the current directory or tell Nix where to find the harness.

`/path/to/project` is a placeholder: replace it with the root of your Git checkout.
No path is assumed or discovered. Quoting the path also supports directory names
containing spaces. `doctor` does not compile or run the project's test suite.

To run from any directory, give Nix the path to the harness clone explicitly:

```sh
nix run "/path/to/langgraph-harness" -- --repo "/path/to/project" doctor
```

If `nix run .` reports that the current directory does not contain a
`flake.nix` and ends with `could not find a flake.nix file`, the harness has not
started yet. Change into the harness clone or use its explicit path as above.
Changing `--repo` will not fix this Nix startup error.

Supported flake systems: Apple Silicon macOS and x86_64/aarch64 Linux.

### Run directly from GitHub

You can use the harness without cloning it. These commands work from any directory:

```sh
nix run github:Devnull-org/langgraph-harness -- help
nix run github:Devnull-org/langgraph-harness -- --repo "/path/to/project" doctor
```

Replace `.` in any `nix run` example with `github:Devnull-org/langgraph-harness`.
Keep the arguments after `--`. An optional package selector such as `#codex`
forces that backend; omit it for automatic selection.
The target project is still a local Git checkout selected with `--repo`.

The same GitHub reference works for a development shell and the harness checks:

```sh
nix develop github:Devnull-org/langgraph-harness
nix flake check github:Devnull-org/langgraph-harness
```

`nix develop` provides tools in the current directory; it does not create a writable
harness clone there. Use a clone to edit the harness or run its local `just` recipes.
`nix flake check` checks the fetched harness source. Commands that modify the harness,
such as `nix flake update` or `nix fmt`, should be run in your harness clone.

The GitHub reference uses the repository's default branch. Nix may cache it; add
`--refresh` to fetch its latest state, for example
`nix run --refresh github:Devnull-org/langgraph-harness -- help`.
To pin the harness version, use `github:Devnull-org/langgraph-harness/COMMIT`,
replacing `COMMIT` with a full commit hash. This selects the harness version;
`start --base` below selects the target project's base.

### Save Hydra project knowledge

Save protocol documentation and state-machine source from your target's committed
HEAD before starting tasks:

```sh
nix run . -- --repo "/path/to/project" context refresh --profile hydra
```

`context refresh` creates a shared, versioned reference snapshot. `--profile hydra`
selects the Head protocol overview, state types, transition handlers, inputs,
effects, event replay, on-chain validators, and rollback documentation. It uses
Git, not a model, and excludes uncommitted edits. See [Project context](#project-context)
for inspection, updates, and custom sources.

### Start with the interview

Choose a target checkout and start a task with your own description. It can contain
staged changes, unstaged changes, and untracked files:

```sh
nix run . -- --repo "/path/to/project" start my-feature \
  "Describe the feature or bug here"
```

| Argument | Meaning |
| --- | --- |
| `--repo "/path/to/project"` | Select the target Git checkout. This is required for task commands. |
| `start` | Create a new task and begin the requirements interview. |
| `my-feature` | Your name for this task, used later by `resume` and `status`. Choose a name not already used for this checkout. |
| `"Describe the feature or bug here"` | Your initial request. Replace this text with what you want, or omit it to have the harness ask you. |

The harness asks for a base branch or commit before the interview. Press Enter to
accept the displayed current branch (`HEAD` for a detached checkout), or enter a
different branch, tag, or commit. To select the base directly:

```sh
nix run . -- --repo "/path/to/project" start my-feature \
  "Describe the feature or bug here" --base master
```

Replace `master` with the base you want. A remote-tracking branch such as
`origin/master` must already exist locally; fetch it yourself if needed. The harness
does not fetch or update branches. Scripts and `--json` require both `--base` and a
request so they never wait for a startup prompt.

**Each phase runs only when you choose it.** `start` runs the interview. The agent
asks questions until the specification is ready for your approval, then saves it
and stops. Approving the specification does not start test authoring.

The harness resolves the selected base to an exact commit and records it with the
latest shared project context snapshot. Each task gets a separate branch and
worktree **before the interview**, so every agent reads the selected base. Local
uncommitted files stay in your original checkout and are excluded from the task.
The original checkout's files, index, and branch stay unchanged. Later changes to
that checkout or its base branch do not change the saved task. `status` displays
the selected base, pinned commit, and worktree path. All later phases reuse that
worktree; you do not provide `--base` again.

### Continue one phase at a time

You can leave after the interview and return another day. Check saved progress:

```sh
nix run . -- --repo "/path/to/project" status my-feature
```

`status` shows the current phase, reports, context snapshot, and available next
phases. It does not invoke an agent. Once the specification is approved, start
test authoring explicitly:

```sh
nix run . -- --repo "/path/to/project" run my-feature tests
```

`run TASK PHASE` starts exactly the named phase. The tests phase writes failing
tests, validates them, and waits for your questions and approval. Approving the
tests commits that patch and stops. Start implementation separately:

```sh
nix run . -- --repo "/path/to/project" run my-feature implement
```

This command requires approved tests and stops after implementation and validation.
The same pattern selects the later phases:

| Phase argument | What it does and when it is available |
| --- | --- |
| `tests` | Write and validate failing tests, discuss them with you, and commit them only after approval. Requires an approved specification or an explicit test revision request. |
| `implement` | Implement the approved tests with validated patches. Requires test approval. |
| `refactor` | Simplify the implementation and comments while keeping tests passing. Requires completed implementation, or a selected review finding that only needs refactoring. |
| `review-haskell` | Review Haskell correctness, performance, style, and maintainability after refactoring. |
| `review-security` | Review Cardano on-chain/off-chain safety, exploits, and protocol liveness after refactoring. The two reviewers can run in either order. |
| `walkthrough` | Ask three to five questions about the finished code and give feedback. Available after both reviews are resolved; normally optional. |
| `handoff` | Write the local PR description and final reports after both reviews are resolved. Does not push or open a PR. |

For example, `run my-feature refactor` starts refactoring; replace only the phase
argument to choose another available phase. Finishing one never starts the next.
Validation, repair attempts, and multiple patches within the chosen phase can run
automatically. A changed checkout between phases blocks continuation: restore the
saved files and revision, or start a new task for the changed checkout.

`resume my-feature` returns to an unfinished phase or its pending question or
approval. It does not select the next phase. Saved tasks, including older completed
interview-only tasks, can continue with `run my-feature tests`.

### Abandon an idea permanently

Type `abandon` at a conversation prompt, or run:

```sh
haskell-harness --repo "/path/to/project" abandon my-feature
```

With Nix, from any directory:

```sh
nix run --refresh github:Devnull-org/langgraph-harness -- \
  --repo "/path/to/project" abandon my-feature
```

The harness shows the task files, worktree, and local branch, then asks you to type
the task name to confirm deletion. Enter cancels. Confirming deletes the saved
conversation and checkpoints, reports, backend diagnostics, task worktree, and
local task branch, including uncommitted files and task commits. There is no
harness archive to resume; you can reuse the task name for a new idea.

The source checkout, other tasks (including separate restart attempts), and shared
project knowledge stay available. Abandonment needs no model backend or login.
If another terminal is using the task, quit that conversation first. The harness
refuses deletion during an active workflow operation or when the worktree/branch
no longer belongs to the task.

For scripts, `abandon my-feature --yes` confirms the same permanent deletion
without prompting. `--yes` is required with `--json` or noninteractive input.
If cleanup fails partway, `status` shows `abandoning`; run `abandon my-feature`
again to finish. Agents cannot resume a task while its deletion is unfinished.

### Start over without losing the previous attempt

Use `restart` when you want a fresh interview. If another terminal is at a
conversation prompt for the old task, type `quit` there first:

```sh
haskell-harness --repo "/path/to/project" restart my-feature
```

This creates `my-feature-restart-1` (or the next unused number), with its own
branch, worktree, checkpoints, and reports. The original task, including any
uncommitted work and commits in its worktree, stays available. The new interview
reuses the original request and exact base commit, even if that branch has moved.
It uses the latest shared project context and keeps the original walkthrough
preference. Specifications, test approvals, patches, and discussions are not
carried into the new attempt. Later phases still require explicit commands.

You can choose the new task name, change the request, or select a different base:

```sh
haskell-harness --repo "/path/to/project" restart my-feature \
  "A revised description" --as my-feature-v2 --base master
```

`--as` chooses the new name; an existing task cannot be overwritten. `--base`
resolves the selected branch, tag, or commit afresh. Omitting either option uses
the defaults above. Use the **new task name** printed by the command for subsequent
`status`, `resume`, `respond`, and `run` commands. Restarting does not delete or
cancel the previous task; it can still be inspected or resumed separately.

### Optional start flags

`--interview-only` is still accepted for compatibility, but it is no longer needed:
`start` already stops after the interview, and you can start tests later on the
same task. An interview prepares the task's worktree but does not write tests or
make commits.

`--walkthrough` records that you want the code questions before handoff. It does
not launch them automatically: choose `run my-feature walkthrough` after reviews.
During the walkthrough, `skip` ends the questions; handoff remains a separate
phase. Without the flag, you can choose either walkthrough or handoff after reviews.

Both flags belong after `start` and its task name. For example:

```sh
nix run . -- --repo "/path/to/project" start my-feature \
  "Describe the feature or bug here" --walkthrough
```

## Other ways to run the harness

The examples below use `haskell-harness` directly. That command is available in a
Nix profile installation, a Nix development shell, or an activated uv environment.
You can use the same arguments after `nix run . --` without entering a shell.

### Install the command with Nix

Install once from any directory, without cloning the harness:

```sh
nix profile add --refresh github:Devnull-org/langgraph-harness
haskell-harness help
```

`nix profile add` installs the package in your user profile, making
`haskell-harness` available in your terminals. `--refresh` asks Nix to fetch the
latest GitHub version. The default package supplies all three built-in CLIs and
uses automatic backend selection. If your Nix version does not recognize
`profile add`, use `profile install` instead. Both command names are supported by
[current Nix](https://nix.dev/manual/nix/2.32/command-ref/new-cli/nix3-profile-add).

To install from your harness clone instead, use `nix profile add .` there.
Use `#local`, `#codex`, or `#claude-code` only to install and force a particular
backend. Install one variant at a time;
they expose the same command names. `hydra-harness` remains a compatibility alias.

If the command is not found after installation, open a new terminal so Nix's
profile is on your `PATH`. `nix run` remains available without a profile install.

### Nix packages and shells

| Command | Purpose |
| --- | --- |
| `nix run . -- backends` | List configured backend names and the default selection. It does not check login or start agents. |
| `nix run .#codex -- doctor` | Supply the Codex CLI through Nix, select its profile, and check its saved login. |
| `nix run .#claude-code -- doctor` | Supply the Claude Code CLI through Nix, select its profile, and check its login. |
| `nix run .#local -- doctor` | Force the legacy `local` backend and check its saved login. |
| `nix develop` | Enter a shell with the harness, development tools, and all built-in agent CLIs. Type `exit` to leave it. |
| `nix develop .#local` | Enter that shell with the legacy `local` CLI. |
| `nix develop .#codex` | Enter that shell with the Codex CLI also available. Select it with `--backend codex`. |
| `nix develop .#claude-code` | Enter that shell with the Claude Code CLI also available. |

The optional `nix run` packages select their matching backend automatically.
Development shells supply the CLI but do not change the configured backend.
To select another installed backend and model explicitly:

```sh
haskell-harness --repo "/path/to/project" --backend claude-code --model opus \
  start my-feature "Describe the feature or bug here"
```

`--backend claude-code` chooses the agent runtime; `--model opus` selects its
model. Both are global options and go before `start`. Pass `--backend` on each
invocation, or set `default_backend` in your user configuration to keep that choice.

Without a terminal, automatic selection succeeds only when exactly one backend
works. Otherwise the command exits with code 2; `--json` includes
`available_backends` and `backend_checks`. A script can rerun with an explicit
`--backend NAME`. `status`, `help`, `backends`, and context commands never perform
availability requests. `doctor` checks tools and logins without generating replies.

Backend profiles, model overrides, per-role routing, authentication probes, and the
protocol for custom model/API bridges are described in [BACKENDS.md](BACKENDS.md).
The workflow and its approval gates are the same for every backend.

### Use your Codex / ChatGPT login

These commands work from any directory, without cloning the harness:

```sh
nix run --refresh github:Devnull-org/langgraph-harness#codex -- doctor
nix run --refresh github:Devnull-org/langgraph-harness#codex -- \
  --repo "/path/to/project" resume my-feature
```

`#codex` supplies Codex and selects the `codex` backend. `doctor` checks the saved
CLI login without generating a model response or checking the remaining usage
allowance. `resume my-feature` opens the existing task with that backend. Replace
the project path and task name with yours, and preserve any `--state-dir` override.
Quit any older harness process first. At a saved `backend_blocked` state, `resume`
retries the failed step once with the selected backend and names the previous
backend in the progress message. Older `agent_error` states still require `retry`.
Your answers, approvals, base, and
worktree remain saved; switching backends does not require `restart`.

If Codex reports that it is not logged in, sign in once with its bundled CLI:

```sh
nix develop github:Devnull-org/langgraph-harness#codex --command codex login
```

This opens Codex's normal login flow. An existing working Codex login needs no
new API key or login. See the official [authentication documentation](https://learn.chatgpt.com/docs/auth).

From a harness clone, use `nix run .#codex --` with the same arguments. Without
Nix, install Codex separately and use `haskell-harness --backend codex doctor`
or `haskell-harness --backend codex --repo "/path/to/project" resume my-feature`.
All subsequent invocations should select the same backend, or install the
`#codex` harness variant to select it by default.

### Without Nix

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git. From
the harness clone, install its dependencies and activate the environment:

```sh
uv sync --locked --no-dev
source .venv/bin/activate
haskell-harness backends
```

| Command | Purpose |
| --- | --- |
| `uv sync --locked --no-dev` | Install the dependencies in `uv.lock` into `.venv`, downloading compatible Python if needed. `--locked` refuses changes to the lockfile; `--no-dev` excludes development tools. |
| `source .venv/bin/activate` | Make `haskell-harness` available in this terminal. Repeat in new terminals; use `deactivate` to leave the environment. |
| `haskell-harness backends` | List configured backend names and the default selection. |

To run a single command without activating the environment:

```sh
uv run --locked --no-dev haskell-harness backends
```

`uv run` prepares the environment and runs the following command with its locked
dependencies. Install and sign in to your chosen agent CLI separately, or
configure a model bridge as described in [BACKENDS.md](BACKENDS.md). The harness
and interviews work without Nix; Hydra's code validation still requires Nix.

## The conversation

During an agent call, an animated spinner shows the backend, agent role, and elapsed
time. It clears before questions or errors appear, including on timeout or Ctrl+C.
The spinner uses stderr; JSON output stays on stdout. With `--json`, redirected
stderr, or `TERM=dumb`, plain-text progress appears every 15 seconds instead.
Backend output may be buffered until the call finishes, so the indicator shows
that the harness is waiting rather than measuring completed work. The configured
agent timeout still applies.

The harness accepts a JSON response, including one object after introductory
commentary or one fenced JSON block with surrounding commentary. It then validates
the complete object against the role's schema. If parsing or validation fails,
the harness asks the same backend for **one corrected response automatically**,
including the validation error and previous output. It preserves the developer's
answers and approval gates. The terminal reports the correction attempt; each
call uses the configured agent timeout. Invalid output larger than 64 KB, or
combined correction context larger than 400 KB, is saved without automatic correction.
No response is applied without successful validation.

If correction fails, the task pauses at `backend_blocked` and keeps the terminal
open. Provider usage limits, authentication failures, rate limits, timeouts, and
other provider errors also pause here, with a specific reason and next step.
A failed task call does not silently switch providers or loop over retries.
Launching `resume` checks and selects a backend again, then retries a saved
`backend_blocked` step once. Human questions and approvals still wait for you.
A login check alone does not establish available quota.

Failed calls are saved under the task's `backend-responses` directory, outside the
checkout. `stdout.txt` and `stderr.txt` preserve the original diagnostics;
`failure.json` records the category and error. A failed correction is saved in
`attempt-2` alongside the original failure. These files can contain task and
source details, so inspect them before sharing. The harness also checks that the
agent did not alter the checkout before allowing a correction.

If an otherwise valid response contains unexpected fields whose values are `null`,
`""`, `[]`, or `{}`, the harness discards those empty additions and prints their
field names. Missing required fields, incorrect types, and unexpected fields with
information require a corrected, valid reply. For example, an extra `out_of_scope_note: []`
does not lose the interview, but it cannot replace the required `out_of_scope` field.

Agent calls run in the background using the selected backend's existing login.
They do not inherit the parent terminal's Orca pane or session identity, so desktop
status hooks cannot report a child call as the parent conversation. A backend CLI
may still save its own session history, which desktop apps can discover and show
as separate, sometimes empty chats. The harness conversation and approval prompts
remain in the terminal where you launched it.

At a blocked prompt, `retry` repeats the step after you address its cause. `quit`
leaves the conversation normally with the task saved. Scripts and `--json` return
exit code 1 for blocked tasks, with a structured `backend_failure` and diagnostics
path for backend failures. Errors in workspace integrity or other workflow
operations remain `agent_error`.

After updating the harness, quit the old process and run the new version to start
or resume a task. `restart TASK` starts a fresh interview under a new name; it is
not needed for the automatic response correction described above.

The terminal shows the current specification, questions, test diff, or review
findings, followed by available actions. Answer interview and walkthrough questions
by typing normal text and pressing Enter. You do not need an `answer` prefix.
At a specification, test, or findings review, plain text asks the agent a question;
use `revise TEXT` to request a change. Everything is read in the same terminal.

Requests, answers, questions, revisions, and written reasons support multiple
lines in the terminal editor:

| Input | Effect |
| --- | --- |
| Enter | Send the whole message. |
| Alt+Enter | Insert a newline. You can also press Escape, release it, then press Enter. |
| Ctrl+J | Insert a newline without using Alt/Option. |
| Paste | Keep paragraphs, blank lines, and indentation together for editing; press Enter when ready to send. |
| Arrow keys | Move through the message to edit it before sending. |
| Ctrl+C | Leave the conversation without sending the current draft. Already submitted messages remain saved. |
| Ctrl+D on an empty prompt | Leave the conversation and resume later. |

On macOS, Option+Enter works when the terminal sends Option as Alt/Meta; otherwise
use Ctrl+J or Escape followed by Enter. Shift+Enter is not reliable across terminals.
These controls also apply to the initial request when you omit it from `start`,
and to the `Your response:` prompt. Backend choices, base branches, and finding
IDs remain single-line inputs.

To write a longer revision, type `revise` and press Enter. The `Your response:`
prompt accepts the full multiline instructions. You can also type `revise TEXT`
and add more lines before sending.

For example, these are lines you can type at the appropriate prompts:

```text
Support negative integers too.
Why does this property cover the empty case?
revise Add coverage for duplicate messages.
approve
quit
```

Only `approve` on its own accepts a specification or test gate. A sentence such as
`approve only after we discuss edge cases` is a discussion message, not approval.
An `approve` line inside a multiline message is text, not a separate command.
Answers, questions, and blank lines never approve anything. Test approval is tied to the exact validated files;
a changed worktree requires `recheck` and fresh approval. You can inspect the full
diff in your editor using the displayed worktree path.

Commands apply only at gates that offer them. The explicit `answer` and `question`
prefixes remain available, including for text that would otherwise match a command:

| Action | Meaning |
| --- | --- |
| Plain text | Answer an interview/walkthrough question, or ask a question at a review gate. |
| `answer TEXT` | Answer an interview or walkthrough question. |
| `question TEXT` | Ask for an explanation of the current specification, tests, or finding without approving it. |
| `revise TEXT` | Request changes to the specification or current patch. A request to change approved tests stops for an explicitly started tests phase. |
| `approve` | Accept the displayed specification or exact validated tests. Type it alone to finish that phase. |
| `recheck` | Rerun validation of the current patch. Changed tests still need fresh approval. |
| `accept_red REASON` | Record evidence that a red-phase Nix failure comes only from the expected failing tests. Test approval remains a separate step. |
| `retry` | Retry the failed operation in the current phase after addressing its error. |
| `reject` | Reject an implementation/refactoring proposal to change approved tests. The active agent must produce another proposal. |
| `fix ID ...` | Select review findings to address. The harness offers the required tests or refactoring phase; start it with `run`. |
| `dismiss ID ...` | Dismiss selected findings with a written reason, which the terminal asks you for. |
| `skip` | End the optional walkthrough questions. |
| `quit` or `exit` | Leave the terminal conversation at its current gate and return later with `resume`. |
| `abandon` | Show the task resources and ask for confirmation to permanently delete the idea and its local work. |

`quit` pauses at the gate. Continue later, including after a terminal interruption:

```sh
haskell-harness --repo "/path/to/project" resume my-feature
haskell-harness --repo "/path/to/project" status my-feature
```

`resume` continues only unfinished work in the selected phase. `status` displays
progress without running work. Both use the task name supplied to `start`.

State defaults to `$XDG_STATE_HOME/hydra-harness` or
`~/.local/state/hydra-harness`, separate from the source checkout. Always use the
same `--repo` and `--state-dir` when resuming. Tasks use separate LangGraph threads
in SQLite, with local worktrees, proposals, logs, and reports alongside them.
Avoid deleting the state directory while its Git worktrees are registered.

## Project context

Project knowledge is shared across tasks in `project-context.sqlite`, alongside
the task checkpoint database. Each refresh stores complete source text, paths,
content hashes, the source Git revision, and a timestamp. It does not modify the
target checkout. The Hydra profile records the actual state constructors from
the selected revision instead of assuming one fixed protocol version.

Show the saved protocol overview, Head state declaration, and source inventory:

```sh
haskell-harness --repo "/path/to/project" context show
```

Read the full saved transition implementation:

```sh
haskell-harness --repo "/path/to/project" context show \
  --source hydra-node/src/Hydra/HeadLogic.hs
```

`--source` here selects a file in the saved snapshot. To save additional general
properties or team notes, commit them in a project document and include its path
when refreshing:

```sh
haskell-harness --repo "/path/to/project" context refresh \
  --profile hydra --source docs/team-properties.md
```

Replace `docs/team-properties.md` with your actual tracked document. Repeat
`--source` for more files. Without `--profile hydra`, only your explicitly selected
sources are saved, which also supports other Haskell projects. Refresh builds a
new complete selection of sources; repeat any extra sources you want to retain.

| Context command | Purpose |
| --- | --- |
| `context refresh --profile hydra` | Save the current committed protocol and implementation sources. Missing optional sources are reported; missing core sources are an error. |
| `context refresh --source PATH` | Save explicitly chosen tracked documents. Repeat `--source` to include more files. |
| `context show` | Inspect the latest selected snapshot's overview and sources. |
| `context show --source PATH` | Read a saved source in full. |
| `context history` | List snapshot IDs, source revisions, and creation times. |
| `context show --snapshot ID` | Inspect a previous snapshot. Add `--source PATH` to read one of its sources. |
| `context use TASK` | Select the latest context for an existing task between phases. |
| `context use TASK --snapshot ID` | Select a specific historical snapshot for that task. |

Replace `TASK`, `PATH`, and `ID` with your task name, source path, and snapshot ID.
All context commands require `--repo`; they work without a model login. Global
`--json` prints structured output instead of the human-readable view.

New tasks pin the currently selected context snapshot. Every agent role receives
source references, symbol names, and bounded excerpts from that same snapshot.
Full sources remain available through `context show --source`. Refreshing the
shared context does not silently change existing tasks. To update a saved task
between phases, use `context use`; a pending question or approval must be resolved
first. Tasks created before project context was added can use this command too.

Before each agent call, source hashes are compared with its current worktree.
Changed sources are listed and their old excerpts are withheld. The agent must
read the current source for those details. Context is reference material, not
proof that code is correct or permission to bypass the approval gates.

## Workflow and review gates

```text
Interview and repository investigation
  → Developer accepts the specification
  → Test author writes failing Haskell tests
  → Lint, strict build, Nix checks, and Cabal tests
  → Developer questions, revises, and approves the tests
  → Commit the approved tests
  → Expert Haskell implementer produces focused, validated patches
  → Separate refactoring agent preserves behavior and simplifies the code
  → Independent Haskell correctness/style and Cardano security/liveness reviews
  → Developer chooses how to address findings; behavior fixes return to TDD
  → Optional developer walkthrough, one question at a time
  → Explicit local handoff
```

The arrows show prerequisites, not automatic transitions. Use `run TASK PHASE`
to choose each phase; the two review phases can run in either order.

Each role has its own prompt under `src/hydra_harness/prompts`. The Haskell agents
are instructed to follow repository conventions, use appropriate Haskell concepts,
avoid speculative abstractions, consider strictness and allocation, and keep
comments short and clear. Reviewers inspect the same final revision separately.
The Cardano reviewer considers both on-chain safety and off-chain protocol
progress, including adversarial sequences that could leave a head stuck.

Findings include location, conditions, event sequence, evidence, impact, confidence,
and a proposed fix. Use `fix` with finding IDs to initiate repairs; regression
changes return to test authoring and your approval. Use `dismiss` only with a
written rationale, which remains in the handoff. Both reviews become required
again after the fix is refactored; start them explicitly with their phase commands.

The walkthrough gives feedback and records the discussion. Finishing or skipping
its questions stops before handoff, which is always a separate phase.

## Hydra validation and commits

Hydra's tests are **Haskell tests run by Cabal**. For each proposed code patch the
harness invokes the following from its Hydra worktree:

```sh
just lint
just check
just test PACKAGE_NAME
```

It invokes `just test` for every package named in the accepted specification.
`PACKAGE_NAME` stands for an actual Cabal package name, such as `hydra-node`.
Hydra's `just lint` includes formatting and a Cabal build with `-Werror`, including
test components enabled by `cabal.project`. `just check` runs Hydra's Nix checks.
The explicit Cabal run is also required for packages such as `hydra-cluster` and
`hydra-tui` which do not have Nix test checks.

These commands run inside **Hydra's** dependency environment. It defaults to the
task worktree's flake, starting from the selected base. To use a separate dependency
environment, pass its path explicitly when running code phases:

```sh
haskell-harness --repo "/path/to/project" --nix-flake "/path/to/dependency-flake" \
  run my-feature tests
```

The `--nix-flake` checkout supplies tools; the worktree supplies the code being
built and tested. Use the same option when resuming. The harness flake does not
replace Hydra's GHC, Cabal, or project dependencies.

Every commit must pass lint and strict compilation. Test-first commits may contain
identified failing assertions; compiler errors, missing test reports, runner
errors, and unrelated failing tests block progress. The harness reads fresh JUnit
reports produced by Hydra's `just test` wrapper and compares exact test identities.
If a Nix check fails during the red phase, progress stops at `red_failure_review`.
Inspect the full logs. `accept_red` requires a written explanation establishing
that only the expected red tests caused that failure. It then returns to test
review, where `approve` is still required. Green phases require every check to pass.

Implementation cannot silently edit approved tests or Cabal wiring. Such proposals
stop at `test_change_review`; `revise` offers the tests phase for discussion. `reject`
sends the developer's decision back to the implementation/refactoring agent.
Uncommitted production changes must be saved or restored before revising tests.
Configuration that controls validation, Git metadata, agent instructions, symlink
paths, and changelogs cannot be edited through agent proposals.

Automated repair attempts are bounded. A failed check preserves the logs and
returns control after three attempts. Individual implementation/refactoring
patches are committed only after validation. Git signing and hooks are honored;
unlock your signing key before running. `--unsigned-commits` is an explicit opt-in
for that invocation and does not change Git configuration.

## Local handoff

The displayed report directory contains:

- `specification.json` and `current-review.json` for the accepted behavior and review gate.
- `validation.json` and full per-command logs, including actual exit codes and JUnit output.
- `patches.json` describing the commit series and validation for each patch.
- Independent `haskell-review.json` and `security-review.json`, with earlier rounds retained.
- `dispositions.json`, `walkthrough.json`, and a self-contained `pr-description.md` draft.

Nothing is pushed and no PR is created. Inspect the branch and reports, then use
your normal publishing process. Agent review is advisory; it does not establish
that a protocol has no security or liveness defects.

## Command and option reference

Every example uses the same argument order:

```text
haskell-harness [global options] COMMAND [command arguments]
```

With Nix, replace `haskell-harness` with `nix run . --`. Task names use 1–64
letters, digits, underscores, or hyphens and must begin with a letter or digit.

| Command | Purpose |
| --- | --- |
| `backends` | List configured backend profiles and the default selection. No checkout is needed. |
| `help` | Walk through setup, base selection, approvals, phases, and resuming with examples. No checkout, backend configuration, or login is needed. |
| `doctor` | Check tools and backend authentication probes. With `--repo`, also check the target's Nix requirements. Does not build or run tests. |
| `start TASK [REQUEST]` | Choose a base, create an isolated task worktree, and run the interview. In a terminal, ask for the base and request if omitted. |
| `restart TASK [REQUEST]` | Start a new task from the original request and pinned base, leaving the original intact. Optional `REQUEST` replaces the original description. Prints the new task name. |
| `abandon TASK` | Confirm and permanently delete the saved task, reports, local branch, and worktree. No model backend is needed. |
| `run TASK PHASE` | Start one available phase of a saved task. It cannot bypass prerequisites or approvals. |
| `resume TASK` | Select a usable backend and retry a saved backend failure once, continue interrupted work, or return to a pending human gate. A completed phase remains stopped. |
| `status TASK` | Display saved progress and the available next phases without invoking agents. |
| `respond TASK ACTION` | Send one explicit response to the current gate, then return. |
| `context refresh/show/history/use` | Manage the shared project knowledge described above. |

Global options go **before** the command:

| Option | Meaning and default |
| --- | --- |
| `--repo PATH` | Root of the target Git checkout. Required for task and context commands; there is no default path. |
| `--state-dir PATH` | Base directory for SQLite data, worktrees, logs, and reports. Defaults to `$XDG_STATE_HOME/hydra-harness` or `~/.local/state/hydra-harness`. Must be outside the target checkout. Use the same value on later invocations. |
| `--nix-flake PATH` | Target dependency flake used for validation. Defaults to the task worktree (`--repo` for `doctor`, which has no task). Use the same override when resuming or running later code phases. |
| `--backend NAME` | Force a backend for every agent. `auto` checks all profiles and asks if several work; automatic selection is the default. Named user defaults and per-role routing remain available. |
| `--model ID` | Override the model for every agent. Otherwise use the selected backend and role configuration. |
| `--config FILE` | Load a user-owned backend TOML file. Without it, use the optional standard user configuration described in [BACKENDS.md](BACKENDS.md). |
| `--agent-timeout SECONDS` | Override each agent call's timeout. Otherwise use the backend setting; built-in profiles default to 600 seconds. |
| `--check-timeout SECONDS` | Maximum time for each validation command. Default: 3600 seconds. |
| `--unsigned-commits` | Disable signing only for commits made by this invocation. Otherwise respect Git signing settings. |
| `--json` | Print structured results and leave interactive replies to `respond`. Work requested by `start`, `run`, or `resume` still runs up to a gate or phase boundary. |
| `--help` | Show command help and exit. Put it after a subcommand for that command's options. |

Options specific to `start`:

| Option | Meaning |
| --- | --- |
| `--base REF` | Base branch, tag, or commit. Asked in a terminal if omitted, with the current branch as the default (`HEAD` if detached). Required with `--json` or noninteractive input. Saved as an exact commit for all later phases. |
| `--interview-only` | Compatibility option. Starting already stops after the interview, and the same task can continue later. |
| `--walkthrough` | Require an explicitly started walkthrough before handoff. You can still use `skip` during the questions. |

Options specific to `restart`:

| Option | Meaning |
| --- | --- |
| `--as NAME` | New task name. Defaults to `TASK-restart-N`, using the next free number and shortening a long prefix to fit the task-name limit. Existing tasks are never overwritten. |
| `--base REF` | Resolve a different base branch, tag, or commit. Defaults to the original task's exact saved commit, with no startup prompt. |

Options specific to `abandon`:

| Option | Meaning |
| --- | --- |
| `--yes` | Confirm permanent deletion of the task, worktree, local branch, and task files, including uncommitted changes. Skips the confirmation prompt; required with `--json` or noninteractive input. |

Options specific to `respond`:

| Option | Meaning |
| --- | --- |
| `--text TEXT` | The answer, question, requested revision, dismissal reason, or red-failure explanation. |
| `--token TOKEN` | Current approval token, required for `approve` and `accept_red`. Read `prompt.approval_token` from `--json status`. |
| `--finding ID` | Finding to fix or dismiss. Repeat the option for multiple IDs. |

For example, `nix run . -- run --help` lists phase names and
`nix run . -- start --help` explains the start options.

## Scripted responses

Start a task without interactive startup prompts:

```sh
haskell-harness --repo "/path/to/project" --json start my-feature \
  "Describe the feature or bug here" --base master
```

Get the saved gate as JSON:

```sh
haskell-harness --repo "/path/to/project" --json status my-feature
```

Ask a question about that gate without approving it:

```sh
haskell-harness --repo "/path/to/project" --json respond my-feature question \
  --text "What happens if a participant disconnects?"
```

Read status again after the reply, then approve with its current token. Replace
`TOKEN_FROM_STATUS` with the value of `prompt.approval_token`:

```sh
haskell-harness --repo "/path/to/project" --json respond my-feature approve \
  --token "TOKEN_FROM_STATUS"
```

Tokens are tied to the gate and validated content; an old token cannot approve a
new version. Each agent invocation uses the selected backend with a role-specific
prompt and schema-validated JSON response. Switching a backend does not change
saved workflow state or bypass an approval gate.

## Develop and check the harness

Nix supplies the complete development environment:

```sh
nix develop
just lint
just test
just check
```

In the harness repository, `just lint` checks Python lint, Python formatting, and
Nix formatting; `just test` runs the harness's Python tests; `just check` runs its
Nix flake checks. These are different recipes from those in a Hydra checkout.

Or run the reproducible check directly:

```sh
nix flake check
nix build
```

`nix flake check` validates the harness flake and runs its test/lint checks.
`nix build` builds the default runnable package and links it as `result`.

Without Nix, run the Python tests and lint checks using uv:

```sh
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Here `pytest` runs the tests, `ruff check .` checks Python lint, and
`ruff format --check .` verifies formatting without rewriting files. `uv run
--locked` supplies development dependencies from the existing lockfile.

The Nix formatting and flake checks still run through the Nix commands above.

The harness's own tests are Python tests for orchestration, approval gates,
process handling, and recovery. They use temporary repositories and scripted
agents/build results. They never contact a model, access credentials, or compile
Hydra. This suite tests the harness's control flow, not the quality of generated
Haskell or security findings. The separate Hydra checks above are always Cabal/Nix.

For dependency changes, run `uv lock`, review the lockfile changes, and rerun
the Python suite and `nix flake check`. Both installation paths use `uv.lock`.
The older `hydra-harness` command remains an alias; existing state and configuration
directories keep their names so saved tasks continue to work.

The built-in backend profiles request read-only inspection. The harness validates
proposed edits and checks for unexpected checkout changes. This is not an OS
sandbox: custom backends run with the capabilities of their local process and
integrations. Backend configurations are user-owned and are never automatically
loaded from the target repository. Use trusted backends and review the diffs.

Completed interview-only tasks can continue with `run TASK tests`. Interviews saved
before base selection was introduced keep their existing checkout checks until
their worktree is created; start a new task with `--base` to use isolation from the
beginning. Older tasks saved in automatic full-workflow mode retain that behavior;
new tasks stop between
phases. General migration across future checkpoint format versions is not provided.
If a commit hook changes content, the harness stops for revalidation.
Large tasks should be split: each agent response is one focused full-file patch,
with bounded context and a maximum of twelve automatic incremental patches.

## References

- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [uv2nix](https://pyproject-nix.github.io/uv2nix/)
