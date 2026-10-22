# Validation and commits

Before writing tests, coding, or running validation, the harness inspects the
project using read-only file access. It reads repository instructions, Cabal
test suites and runner entry points, Nix shells and checks, CI, and documented
recipes. It records suite names, frameworks, prerequisites, commands, and sources
under **Project test setup** in the task. No project commands run during discovery.
If the setup is unclear, the harness asks focused questions before proceeding.

Project tests are **Haskell tests run by Cabal**. Commands run in the task worktree,
inside `nix develop WORKTREE --command ...`. The selected commands must cover lint,
strict compilation with `-Werror`, Nix checks, and each selected package's tests.
HLint and `just` are not assumed. For example, a repository can provide lint through
a Nix check without putting its linter in the development shell.

Discovery is saved in `reports/validation-setup.json` and supplied to coding agents.
Resume reuses it while its sources, relevant configuration, selected packages, and
evidence requirements are unchanged. Changed setup is inspected again. Imported
tasks rediscover it on the receiving machine. Older paused tasks rediscover the
old built-in defaults on **Recheck**, preserving saved non-default command overrides.

Checks can be adapted using `[validation]` in your user configuration. Select a
project-specific configuration explicitly with `--config /path/to/config.toml`;
these explicit overrides take precedence over discovered commands.
For example, a team with existing recipes can use:

```toml
[validation]
lint = ["just", "lint"]
build = ["just", "build-strict"]
nix = ["just", "check"]
test = ["just", "test", "{package}"]
junit_glob = "test-reports/{package}/**/*.xml"
```

Each command is an argument list, executed without a shell. `{package}` in `test`
and `junit_glob` is replaced by the current package name. A whole `{package}` argument
in `build` expands to the selected packages as separate arguments, for `cabal build`.
The build command must
compile the code and tests with warnings treated as errors. Lint, build, Nix,
and test checks are all required; empty command lists are rejected.
Explicit overrides are saved with new tasks and reused on resume. To change these
overrides, start a fresh attempt with the updated configuration.

When reusing existing tests, a successful Cabal command with a complete, nonempty
passing suite summary can establish green. JUnit is not required for that path.
Test-first and standalone coverage phases need individual test identities. The
current detailed-evidence adapter supports JUnit. Discovery checks whether the
runner already supports it and selects its actual flag and report location. If
the project has no supported reporter, discovery pauses to discuss the limitation;
it does not install dependencies, change the runner, or guess flags. Cabal itself
does not automatically produce JUnit for every framework. Only fresh reports inside
the worktree count. A nonzero exit code alone cannot establish an expected assertion.
Keep generated reports and build artifacts ignored by Git so they are not
treated as source changes during validation.

The dependency environment defaults to the task worktree's flake. To supply tools
from a separate flake, pass `--nix-flake /path/to/dependency-flake`. Commands still
run in the task worktree, so the default `nix flake check` checks that worktree.
For a project without its own flake, configure `validation.nix` to check the
appropriate dependency flake explicitly. Use the same `--nix-flake` when reopening the website.

Every commit must pass lint and strict compilation. Test-first commits may contain
identified failing assertions; compiler errors, missing required test evidence, runner
errors, and unrelated failing tests block progress. The harness reads fresh JUnit
reports produced by the configured test command and compares exact test identities.
If a Nix check fails during the red phase, progress stops at `red_failure_review`.
Inspect the full logs. **Accept expected test failure** requires a written explanation establishing
that only the expected red tests caused that failure. It then returns to test
review, where **Approve tests** is still required. Green phases require every check to pass.

Standalone **Write tests** follows the same approval and commit checks. Failing
regression tests use the red rules above; passing coverage tests require every
check to pass and declare no expected failures. Every named coverage test must
appear in the execution evidence. Approval commits tests and finishes the task;
it never starts implementation. Standalone reviews inspect code only and do not
run these commands or claim validation results.

Implementation cannot silently edit approved tests or Cabal wiring. Such proposals
stop at `test_change_review`; **Revise instructions** offers the tests phase for discussion. **Reject changes**
sends the developer's decision back to the implementation/refactoring agent.
Uncommitted production changes must be saved or restored before revising tests.
Configuration that controls validation, Git metadata, agent instructions, symlink
paths, and changelogs cannot be edited through agent proposals.

Automated repair attempts are bounded. A failed check preserves the logs and
returns control after three attempts. Individual implementation/refactoring
patches are committed only after validation. Git signing and hooks are honored;
unlock your signing key before running. `--unsigned-commits` is an explicit opt-in
for that invocation and does not change Git configuration.

