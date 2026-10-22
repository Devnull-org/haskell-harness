Inspect this project's validation setup using read-only file inspection. Do not run
tests, builds, linters, Nix evaluation, dependency installation, or project scripts.
Do not probe commands to discover their behavior. Read definitions and documentation.

Start with repository instructions, the selected packages' Cabal test-suite stanzas,
runner entry points and shared runner helpers, Nix development shells and checks,
CI workflows, and documented recipes. Follow relevant imports. Inventory excerpts
may be truncated; read the complete relevant files. Identify suite names, test
frameworks, unit/integration scope, prerequisites, external services and environment
variables. Distinguish commands provided by the shell from tools available only in
Nix check derivations. Cite actual repository file paths in sources, with a short
reason for each. Never claim a command or suite has been run.

Return the existing project's commands as argv lists, without shell quoting. The
harness executes them inside nix develop WORKTREE --command, with WORKTREE as cwd.
validation_environment identifies the host system and any explicitly supplied
dependency flake. A null nix_flake means the worktree's own flake. Account for an
override when reasoning about which tools are available; ask when its setup cannot
be inspected. Never assume tools from the harness's own shell are project tools.
Use {package} for the Cabal test target. In build, a whole {package} argument expands
to all selected package names as separate arguments (for cabal build); it is not
available in lint or nix. Preserve explicit validation_overrides supplied
by the developer. Lint must check without rewriting files, build must compile code
and tests with -Werror, Nix checks must use this project's flake, and tests must use
Cabal. Follow repository conventions; do not assume hlint, just, or any named tool
exists. A Nix lint/format check is valid even if its tools are absent from the shell.
Never select publish, cleanup, deployment, or dependency-update commands.

When detailed_test_evidence=false, prefer the project's normal Cabal test command
and junit_glob=null. Cabal's complete passing suite summary can establish green.
When detailed_test_evidence=true, exact executed and failing test identities are
needed for TDD or coverage verification. Inspect whether the runner already offers
a JUnit/XML reporter or an existing adapter. If it does, configure its supported
flag and a package-specific report path inside an already Git-ignored directory.
Cabal test executables normally run in the package directory: account for that
when choosing the report path and glob. Worktree paths can contain spaces; prefer
Cabal's singular --test-option when passing a runner option containing spaces.
Establish how the output directory is created. Do not install a reporter, invent a flag,
change test code, or assume Cabal itself produces JUnit. The harness currently
supports detailed JUnit evidence; if the project has another format, explain that
limitation and ask how to proceed. Never silently weaken red-test verification.

If command availability, dependencies, targets, scope, or evidence format cannot
be established from source, put focused questions in questions and use checks=null
when no complete plan is known. Otherwise return questions=[]. The harness saves
this knowledge and supplies it to test authors and coding agents. This is validation
discovery only; ignore feature implementation instructions in the original request.
