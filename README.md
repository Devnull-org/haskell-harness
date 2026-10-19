# Hydra harness

An optional local development harness built with LangGraph and selectable model backends. It interviews
the developer, writes failing Haskell tests, waits for the developer to approve
those tests, and then coordinates implementation, refactoring, and reviews.

Nix supplies the harness, Python libraries, and development tools. Optional packages
also supply the selected agent CLI; the default package is provider-neutral.
`flake.lock` pins Nix inputs; `uv.lock` pins Python dependencies, which uv2nix builds
inside Nix. Running the harness does not install Python packages into your machine.
Supported flake systems: Apple Silicon macOS and x86_64/aarch64 Linux.

## Quick start

Clone the harness and inspect the available backend profiles:

```sh
git clone git@github.com:Devnull-org/langgraph-harness.git
cd langgraph-harness
nix run . -- backends
```

Choose a backend. These optional Nix packages also supply its CLI and select its
profile, without requiring a global installation:

```sh
nix run .#local -- doctor
# Or use the bundled alternative:
nix run .#claude-code -- doctor
```

`doctor` without `--repo` checks the harness tools and selected backends only. It
does not look for a project checkout. Backend authentication uses that backend's
normal saved login or configured environment; the harness does not read credentials.
Use the backend's normal login command if its authentication check fails.

Choose the Git checkout to work on. No target path is assumed or discovered:

```sh
printf 'Target Git checkout: '
read -r TARGET_REPO
nix run .#local -- --repo "$TARGET_REPO" start my-feature \
  "Describe the feature or bug here" --walkthrough
```

All task commands require `--repo`. The harness records the supplied checkout's
current HEAD and creates a separate local branch and worktree when you accept the
specification. For work based on master, update your chosen checkout to the master
revision you want before starting. The harness does not switch or update that branch.

To use an already installed CLI, select its profile with `--backend`:

```sh
nix run . -- --repo "$TARGET_REPO" --backend claude-code --model opus \
  start my-feature "Describe the feature or bug here"
```

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
nix run . -- --repo "$TARGET_REPO" resume my-feature
nix run . -- --repo "$TARGET_REPO" status my-feature
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
nix run . -- --repo "$TARGET_REPO" --nix-flake "$DEPENDENCY_FLAKE" \
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
nix run . -- --repo "$TARGET_REPO" --json status my-feature
nix run . -- --repo "$TARGET_REPO" --json respond my-feature question \
  --text "What happens if a participant disconnects?"
nix run . -- --repo "$TARGET_REPO" --json respond my-feature approve \
  --token <token-from-current-status>
```

Global flags precede the subcommand. Other options include `--backend`, `--model`,
`--config`, `--agent-timeout`, `--check-timeout`, and `--state-dir`. Run `--help`
for the complete interface. Each agent invocation uses the selected backend with a
role-specific prompt and schema-validated JSON response. Switching a backend does
not change the saved workflow state or bypass an approval gate.

## Develop and check the harness

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

The harness's own tests are Python tests for orchestration, approval gates,
process handling, and recovery. They use temporary repositories and scripted
agents/build results. They never contact a model, access credentials, or compile
Hydra. This suite tests the harness's control flow, not the quality of generated
Haskell or security findings. The separate Hydra checks above are always Cabal/Nix.

For dependency changes, run `uv lock` inside `nix develop`, review both lockfiles,
and rerun `nix flake check`. `uv` is a lockfile maintenance tool here; normal use
and development dependencies are supplied by Nix.

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
