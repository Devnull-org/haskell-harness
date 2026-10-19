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

Clone the harness:

```sh
git clone git@github.com:Devnull-org/langgraph-harness.git
cd langgraph-harness
```

### Without Nix

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git, then:

```sh
uv sync --locked --no-dev
source .venv/bin/activate
haskell-harness backends
```

uv downloads a compatible Python if necessary and installs the locked dependencies
in this clone's `.venv`. Activate that environment in each new terminal before
using `haskell-harness`. You can also run a command without activating it:

```sh
uv run --locked --no-dev haskell-harness backends
```

Install and sign in to your chosen agent CLI separately, or configure a bridge to
your model service. Backend setup is described in [BACKENDS.md](BACKENDS.md).

### With Nix

Enter the development environment to use the same `haskell-harness` commands:

```sh
nix develop
haskell-harness backends
```

Alternatively, `nix run . -- backends` runs the harness and lists its configured
backend profiles. These optional packages also supply the selected agent CLI:

```sh
nix run .#local -- doctor
# Or use the bundled alternative:
nix run .#claude-code -- doctor
```

Supported flake systems: Apple Silicon macOS and x86_64/aarch64 Linux.

### Start a task

Check your selected backend:

```sh
haskell-harness doctor
# To select a different backend:
haskell-harness --backend claude-code doctor
```

`doctor` without `--repo` checks the harness tools and selected backends only. It
does not require Nix or look for a project checkout. Add `--repo` to also check
the target's Nix requirements. Backend authentication uses that backend's
normal saved login or configured environment; the harness does not read credentials.
Use the backend's normal login command if its authentication check fails.

Choose the Git checkout to work on. No target path is assumed or discovered:

```sh
printf 'Target Git checkout: '
read -r TARGET_REPO
haskell-harness --repo "$TARGET_REPO" start my-feature \
  "Describe the feature or bug here" --walkthrough
```

To stop after agreeing on a specification, add `--interview-only`; that mode
works without Nix. A full Hydra task needs Nix once code validation begins.

All task commands require `--repo`. The harness records the supplied checkout's
current HEAD and creates a separate local branch and worktree when you accept the
specification. For work based on master, update your chosen checkout to the master
revision you want before starting. The harness does not switch or update that branch.

To use an already installed CLI, select its profile with `--backend`:

```sh
haskell-harness --repo "$TARGET_REPO" --backend claude-code --model opus \
  start my-feature "Describe the feature or bug here"
```

Pass `--backend` on each invocation, or set `default_backend` in your user
configuration to keep using that profile.

Backend profiles, model overrides, per-role routing, authentication probes, and the
protocol for custom model/API bridges are described in [BACKENDS.md](BACKENDS.md).
The workflow and its approval gates are the same for every backend.

## The conversation

The terminal shows the current specification, questions, test diff, or review
findings, followed by available actions. Type an action and optionally its text:

```text
answer Support negative integers too.
question Why does this property cover the empty case?
revise Add coverage for duplicate messages.
approve
quit
```

Only `approve` accepts a specification or test gate. Answers, questions, and
silence never approve anything. Test approval is tied to the exact validated files;
a changed worktree requires `recheck` and fresh approval. You can inspect the full
diff in your editor using the displayed worktree path.

`quit` pauses at the gate. Continue later, including after a terminal interruption:

```sh
haskell-harness --repo "$TARGET_REPO" resume my-feature
haskell-harness --repo "$TARGET_REPO" status my-feature
```

State defaults to `$XDG_STATE_HOME/hydra-harness` or
`~/.local/state/hydra-harness`, separate from the source checkout. Always use the
same `--repo` and `--state-dir` when resuming. Tasks use separate LangGraph threads
in SQLite, with local worktrees, proposals, logs, and reports alongside them.
Avoid deleting the state directory while its Git worktrees are registered.

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
  → Local handoff
```

Each role has its own prompt under `src/hydra_harness/prompts`. The Haskell agents
are instructed to follow repository conventions, use appropriate Haskell concepts,
avoid speculative abstractions, consider strictness and allocation, and keep
comments short and clear. Reviewers inspect the same final revision separately.
The Cardano reviewer considers both on-chain safety and off-chain protocol
progress, including adversarial sequences that could leave a head stuck.

Findings include location, conditions, event sequence, evidence, impact, confidence,
and a proposed fix. Use `fix` with finding IDs to initiate repairs; regression
changes return to test authoring and your approval. Use `dismiss` only with a
written rationale, which remains in the handoff. Reviews run again after fixes.

`--walkthrough` enables three to five questions about the actual change. It gives
feedback after each attempt and records the discussion. `skip` finishes without
completing the walkthrough. `--interview-only` stops after accepting the specification.

## Hydra validation and commits

Hydra's tests are **Haskell tests run by Cabal**. For each proposed code patch the
harness invokes the following from its Hydra worktree:

```sh
just lint
just check
just test <package>
```

It invokes `just test` for every package named in the accepted specification.
Hydra's `just lint` includes formatting and a Cabal build with `-Werror`, including
test components enabled by `cabal.project`. `just check` runs Hydra's Nix checks.
The explicit Cabal run is also required for packages such as `hydra-cluster` and
`hydra-tui` which do not have Nix test checks.

These commands run inside **Hydra's** dependency environment. It defaults to the
explicitly selected checkout's flake. To use a separate dependency environment,
set `DEPENDENCY_FLAKE` to the flake path you choose and pass it explicitly:

```sh
haskell-harness --repo "$TARGET_REPO" --nix-flake "$DEPENDENCY_FLAKE" \
  start fix-recovery "Describe the recovery issue"
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
stop at `test_change_review`; `revise` sends them to the test author. `reject`
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

## Automation and options

`--json` prints the current gate and exits. Explicit replies can be sent separately:

```sh
haskell-harness --repo "$TARGET_REPO" --json status my-feature
haskell-harness --repo "$TARGET_REPO" --json respond my-feature question \
  --text "What happens if a participant disconnects?"
haskell-harness --repo "$TARGET_REPO" --json respond my-feature approve \
  --token <token-from-current-status>
```

Global flags precede the subcommand. Other options include `--backend`, `--model`,
`--config`, `--agent-timeout`, `--check-timeout`, and `--state-dir`. Run `--help`
for the complete interface. Each agent invocation uses the selected backend with a
role-specific prompt and schema-validated JSON response. Switching a backend does
not change the saved workflow state or bypass an approval gate.

## Develop and check the harness

Nix supplies the complete development environment:

```sh
nix develop
just lint
just test
just check
```

Or run the reproducible check directly:

```sh
nix flake check
nix build
```

Without Nix, run the Python tests and lint checks using uv:

```sh
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

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

The SQLite format and workflow state are currently version 0.1. Finish existing
tasks before upgrading the harness; cross-version checkpoint migration is not
implemented. If a commit hook changes content, the harness stops for revalidation.
Large tasks should be split: each agent response is one focused full-file patch,
with bounded context and a maximum of twelve automatic incremental patches.

## References

- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [uv2nix](https://pyproject-nix.github.io/uv2nix/)
