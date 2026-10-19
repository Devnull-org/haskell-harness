# Hydra harness

An opt-in local development harness using LangGraph for workflow state and Cursor
for agent execution. Each developer uses their own Cursor CLI login.

**Status: acceptance tests awaiting developer review.** The public interface is a
placeholder. There is no working CLI or agent workflow yet. The tests intentionally
fail until the implementation is approved and written.

## Agreed workflow

```text
Interview and repository investigation
  → Developer accepts the specification
  → Test author writes failing tests
  → Lint, Nix checks, and relevant tests
  → Developer discusses and approves the tests
  → Commit the approved tests
  → Haskell implementer produces small validated patches
  → Separate refactoring agent produces small validated patches
  → Independent Haskell and Cardano security reviews
  → Resolve findings through the same TDD and approval process
  → Optional developer walkthrough
  → Local handoff for the developer's final review
```

Publishing remains a developer action. Agents do not push branches, create PRs, or
post review comments. The harness prepares local review reports and a PR description.

## Test review

There are two test layers:

- **Hydra feature and regression tests** are Haskell tests run by Cabal. The
  harness invokes the repository's `just test <package> [pattern]` wrapper, which
  calls `cabal test` and produces test reports. These are the tests the developer
  reviews before an agent implements a Hydra change.
- **Harness acceptance tests** are Python tests for LangGraph orchestration,
  approval gates, persistence, and process handling. The suite described below
  tests the harness itself.

The acceptance tests use temporary Git repositories, scripted agent responses, and
simulated build results. They exercise orchestration without contacting a model,
using credentials, compiling Hydra, or modifying the developer's source checkout.
They do not measure the quality of model-generated Haskell or security findings.

| Contract | Test file |
| --- | --- |
| Interview, specification acceptance, test questions and explicit approval | `tests/test_workflow.py` |
| Stale approvals, restart/resume, isolated worktrees, focused commits | `tests/test_workflow.py` |
| Separate implementation, refactoring, and independent review roles | `tests/test_workflow.py` |
| Local findings, protected tests, optional walkthrough | `tests/test_workflow.py` |
| Strict validation, expected red failures, missing or misleading evidence | `tests/test_validation.py` |
| Cursor login reuse, process errors, malformed output, timeouts | `tests/test_cursor.py` |

Run the proposed tests:

```sh
cd tools/hydra-harness
uv sync --python 3.13
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

No tests are skipped or marked as expected failures. The intended red state must be
visible. `contracts.py` contains only interfaces and placeholders so test collection
and Python compilation succeed before the behavior exists.

## Implementation decisions for review

- Keep task state, logs, and worktrees outside the source checkout. Persist
  LangGraph checkpoints in SQLite and identify each task by a stable thread ID.
- Use Cursor's read-only mode for reasoning and repository inspection. Agents
  propose structured file changes; the harness applies them in a dedicated
  worktree, validates them, and creates local commits. This makes approved-test
  protection enforceable at the file-writing boundary.
- Bind approvals to the specification and exact test revision. A question is not
  approval. Edits after validation require fresh checks and approval.
- Run `just lint`, `just check`, and `just test <package>` for relevant packages at
  each code-producing stage and proposed commit. Hydra's `cabal.project` enables
  tests and benchmarks, so the strict build also compiles those components.
- Keep actual exit codes and test output. A red phase requires identified failing
  tests, successful lint/build, and no unrelated test failures. A failed Nix check
  requires explicit inspection of its logs; a nonzero exit alone cannot establish
  that it failed only because of the expected red tests.
- Preserve human-approved tests during implementation and refactoring. Route
  proposed test changes back to the developer before applying them.
- Review the same final revision independently. Security findings distinguish
  suspected issues, reproducible exploits, documented protocol assumptions, and
  newly introduced liveness failures.
- Bound automated repair loops and preserve failures for developer intervention.
- Ask walkthrough questions one at a time, let the developer attempt an answer,
  and provide feedback. The developer may skip the walkthrough.

## Prerequisites for live evaluation

Run `cursor-agent login` and check `cursor-agent status` in the developer's own
terminal. The wrapper will use that saved login, without reading credentials or
forwarding `CURSOR_API_KEY`.

Run Hydra validation in its development environment (`nix develop`) so `just`,
`cabal`, and `nix-fast-build` are available. The harness will not label missing tools
or failed Nix evaluation as passing validation.

## Standalone distribution

Once implemented and reviewed, this package will move into a separate repository.
Developers will clone the harness and point it at their own Hydra checkout. The
harness's Python environment and task state remain separate from Hydra's Cabal/Nix
environment. Setup instructions will cover installation, Cursor login, and checks
for the required development tools.

## References

- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Cursor CLI](https://cursor.com/docs/cli/overview)
- [Cursor output format](https://cursor.com/docs/cli/reference/output-format)
