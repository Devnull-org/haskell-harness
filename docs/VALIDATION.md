# Validation and commits

Project tests are **Haskell tests run by Cabal**. Each code patch runs these
commands in its worktree, inside `nix develop WORKTREE --command ...`:

```sh
hlint .
cabal build all --enable-tests --ghc-options=-Werror
nix flake check
cabal test PACKAGE_NAME --test-show-details=direct
```

The test command runs for each Cabal package in the accepted specification or
the developer's standalone test scope.
`PACKAGE_NAME` is your actual package name. The project's development shell must
provide its GHC, Cabal, HLint, and other required tools. No `justfile` is assumed.
The harness's own Nix environment does not supply project dependencies.

Checks can be adapted using `[validation]` in your user configuration. Select a
project-specific configuration explicitly with `--config /path/to/config.toml`;
the harness never automatically executes configuration from the target checkout.
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
and `junit_glob` is replaced by the current package name. The build command must
compile the code and tests with warnings treated as errors. Lint, build, Nix,
and test checks are all required; empty command lists are rejected.
Commands and report patterns are saved with new tasks and reused on resume.
To use changed settings, start a new task or a fresh attempt. Older saved tasks
without these settings adopt the selected configuration at their next validation.

Cabal does not produce JUnit reports automatically for every test framework.
Configure the project's test runner or a trusted wrapper to write them.
The default report pattern is
`test-reports/**/*.xml`; only fresh files inside the worktree are read. Adapt the
pattern to your runner. Missing reports block approval; a nonzero exit code alone
cannot establish that the intended assertion failed.
Keep generated reports and build artifacts ignored by Git so they are not
treated as source changes during validation.

The dependency environment defaults to the task worktree's flake. To supply tools
from a separate flake, pass `--nix-flake /path/to/dependency-flake`. Commands still
run in the task worktree, so the default `nix flake check` checks that worktree.
For a project without its own flake, configure `validation.nix` to check the
appropriate dependency flake explicitly. Use the same `--nix-flake` when reopening the website.

Every commit must pass lint and strict compilation. Test-first commits may contain
identified failing assertions; compiler errors, missing test reports, runner
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

