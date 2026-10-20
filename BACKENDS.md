# Choosing a backend and model

The workflow depends on an `Agent` callable, not a particular model service. A
backend receives a role, workflow context, and the target worktree. It returns a
validated proposal or review. Only the harness applies patches, runs checks, and
commits. All backend choices share the same developer approval gates.

The command transport supports agent CLIs and small bridges to hosted model APIs.
Prompts, response schemas, validation, process timeouts, and workflow persistence
remain in the harness. Adding a command profile does not require changing Python.

## Select a profile

```sh
haskell-harness backends
haskell-harness --backend claude-code doctor
haskell-harness --repo "/path/to/project" --backend claude-code --model opus \
  start a-task "Describe the requested change"
```

These commands work after either setup in the [quick start](README.md#quick-start).
From the harness clone, you can also use `uv run --locked --no-dev haskell-harness`
or `nix run . --` in place of `haskell-harness`.

Replace `/path/to/project` with the checkout you explicitly select; it has no default. Backend
listing and tool diagnostics do not require a checkout. Adding `--repo` to `doctor`
also checks Nix and that checkout's dependency flake. A separate environment can be chosen
with `--nix-flake "/path/to/dependency-flake"`, where you supply that path too.

The built-in profiles are:

| Profile | Behavior |
| --- | --- |
| `local` | Preserves the existing saved-session CLI integration and its read-only mode. |
| `codex` | Uses Codex with saved CLI authentication, read-only shell access, no interactive approval prompts, and ephemeral sessions. |
| `claude-code` | Uses Claude Code with read/search tools, plan mode, structured output, and no inherited MCP servers. |

When using an existing CLI installation, it is resolved through `PATH`. To let Nix
supply it, use `nix run .#local`, `nix run .#codex`, or `nix run .#claude-code`. These packages select the
matching profile automatically. The default `nix run .` package supplies only the
harness and common tools. The corresponding optional development shells are
`nix develop .#local`, `nix develop .#codex`, and `nix develop .#claude-code`.

The Codex profile uses the documented [non-interactive interface](https://learn.chatgpt.com/docs/non-interactive-mode):
the prompt and role schema go through stdin, progress goes to stderr, and the
final message goes to stdout for the harness's JSON/schema validation. It does
not use `--json`, which produces an event stream instead of a single response.
`--ephemeral` avoids saving a separate Codex session for every harness call.
`--sandbox read-only` limits shell writes, and `--ask-for-approval never` prevents
an unattended call from waiting for a separate approval prompt.

`--ignore-user-config` and `--ignore-rules` skip personal Codex settings and
execpolicy rules for these calls; authentication still uses the existing home
directory and `CODEX_HOME`. Codex chooses its default model unless you select one
with the harness's `--model` option or backend/role configuration. API-key and
access-token environment overrides are omitted from the child process so it uses
the saved CLI login. No global settings or credentials are changed. Personal
gateway or other configuration requirements need a custom profile.

`doctor` runs `codex login status`; it checks authentication, not available quota.
Each provider has its own account and allowance. A working Codex / ChatGPT login
does not provide usage to the `local` backend. To continue an existing task with
Codex, quit the old process and run:

```sh
nix run --refresh github:Devnull-org/langgraph-harness#codex -- \
  --repo "/path/to/project" resume my-feature
```

Use your existing task name, checkout, and any `--state-dir` override. At a saved
`backend_blocked` (or an older `agent_error`), type `retry`; the next call uses Codex.
Switching backends does not discard answers or create a new worktree.

The Claude Code profile uses its documented [headless interface and structured
output](https://code.claude.com/docs/en/headless). Each invocation is independent;
LangGraph owns the workflow's durable state. No provider session is needed to
resume a task with a different backend.

## User configuration

Pass a file with `--config FILE`, or create
`$XDG_CONFIG_HOME/hydra-harness/config.toml` (falling back to the current user's
`~/.config/hydra-harness/config.toml`). The file is optional; its parent directory
does not need to exist. A missing file explicitly selected with `--config` is an
error. Configuration is never automatically read from the target repository.

For example, use a different model for security reviews:

```toml
version = 1
default_backend = "claude-code"

[roles.security_reviewer]
model = "opus"
```

Choose a different backend for a role by adding `backend = "profile-name"` to that
role's table. Names are the prompt names under `src/hydra_harness/prompts`, excluding
`common`. Model IDs are passed to the selected backend without a hard-coded model
catalogue or a silent fallback.

Precedence is explicit:

1. `--backend` selects one profile for all roles, overriding role-specific backend
   and model choices. Its configured default model applies unless `--model` is given.
2. Without `--backend`, each role can override the configured default backend/model.
3. `--model` overrides the model for every role. `--agent-timeout` overrides profile timeouts.

A named profile in your configuration replaces that profile as a whole. Arguments,
credential rules, and response parsing from another provider are never merged into it.

## Connect another CLI or hosted service

For a command that reads a prompt on stdin and returns the role's JSON object:

```toml
default_backend = "team-model"

[backends.team-model]
command = ["team-agent", "--read-only", "--json"]
model_args = ["--model", "{model}"]
model = "your-model-id"
timeout = 600
```

Use real flags supported by your selected tool. Commands are argument arrays,
executed directly without a shell. Each invocation runs in the target checkout or
isolated worktree. The harness never runs a login command automatically.

Profile fields:

| Field | Meaning |
| --- | --- |
| `command` | Required executable and arguments; the executable is resolved through `PATH` or an explicitly configured path. |
| `input_format` | `prompt` (default) for plain prompt text; `json` for the bridge protocol below. |
| `response_fields` | Optional ordered envelope keys, such as `["structured_output", "result"]`. The first present, non-null field holds the response. With no keys, stdout is the response itself. |
| `model_args` | Arguments appended only when a model is selected. `{model}` is replaced with the exact model ID. |
| `model` | Optional default model ID for this profile. |
| `timeout` | Positive process timeout in seconds; default 600. |
| `unset_env` | Environment variable names to omit, for example to prevent API credentials from overriding a saved CLI login. No credential values belong here. |
| `probe` | Optional non-generating authentication/status command for `doctor`. `{executable}` uses the resolved backend executable. |
| `probe_reject_patterns` | Case-insensitive output strings that make a probe fail even if its exit code is zero. |

`command` and `model_args` also support the whole-argument tokens `{schema}` and
`{role}`. `{schema}` is the response schema as JSON. Tokens are replaced only when
they occupy an entire argument; literal JSON arguments are left intact. A prompt
backend with a selected model must configure how to pass it; it cannot silently
ignore a model override.

For a hosted API, use a bridge command. This keeps API credentials and provider
SDKs in that bridge's own environment:

```toml
default_backend = "service"

[backends.service]
command = ["company-model-bridge"]
input_format = "json"
model = "your-hosted-model"
```

The bridge reads one JSON request on stdin:

```json
{
  "version": 1,
  "role": "implementer",
  "model": "your-hosted-model",
  "prompt": "The complete role instructions and task context...",
  "schema": {"type": "object"},
  "context": {"request": "The requested change..."}
}
```

The real schema and context are supplied by the harness. The bridge writes exactly
one JSON object matching that schema to stdout, sends diagnostic logs to stderr,
and exits nonzero on failure. A bridge must provide read-only repository inspection
if its underlying model has no file tools: it runs in the selected worktree and can
read the files there. The harness does not automatically upload a repository to an
API or grant a plain language model file access.

Envelope responses can contain a JSON object or JSON text in the configured field.
Introductory commentary followed by one JSON object, or one fenced JSON block with
surrounding commentary, is also accepted. For an unfenced response, the first
opening brace must begin the complete object and only whitespace may follow it;
the parser never falls back to an object nested inside malformed JSON. Multiple
objects or blocks are rejected. The extracted object must still match the complete
role schema. When the only validation errors are unexpected fields containing
`null`, `""`, `[]`, or `{}`, those fields are removed, their paths are reported,
and the result is validated again. Known fields are unchanged. Nonempty additions,
missing required fields, and type errors remain errors; `false` and `0` are not
treated as empty. Backend configuration remains strict as well.
The workflow requests one automatic correction after invalid JSON/schema output,
using the same role and backend with an additional `context.response_repair` object:
`validation_error` describes the failure and `previous_response` holds the invalid
output. Original context and human approval requirements remain in force. Bridges
using JSON input receive these fields in the versioned request's `context`.
Correction is skipped for output over 64 KB or combined context over 400 KB. Each invocation
has its own configured timeout. There is no automatic retry after a provider
failure, error envelope, process launch failure, or timeout.

Failures are saved in the task's `backend-responses` directory, including raw
stdout/stderr, partial output on timeout, and a `failure.json` record. A failed
correction uses an `attempt-2` subdirectory so it cannot overwrite the first
response. Before any correction, the workflow verifies that the checkout is
unchanged. A valid response continues to the original human gate; invalid output
never becomes a proposal.

Interactive terminals show a
spinner with the agent role and elapsed time. With `--json`, redirected stderr, or
`TERM=dumb`, progress is printed every 15 seconds instead. These indicators show
waiting time, not how much work the model has completed, and never use stdout.
Unresolved failures pause at `backend_blocked`. The prompt includes
`backend_failure` with `backend` and `kind`, a `guidance` message, and a `diagnostics`
path. Kinds are `quota`, `authentication`, `rate_limit`, `timeout`, `unavailable`,
`invalid_response`, and `provider` for errors without a recognized category.
Classification uses explicit error text; it does not claim to know the account's
remaining balance. Scripts still return nonzero for blocked work. Interactive
users can retry after addressing the cause or quit normally. There are no automatic
provider fallbacks. Process groups are terminated on timeout; changing the backend
does not weaken test protection or validation.

Configure required credentials through the selected CLI/service's normal mechanism.
All profiles remove inherited `ORCA_AGENT_*`, `ORCA_PANE_KEY`, `ORCA_TAB_ID`,
`ORCA_WORKTREE_ID`, and `ORCA_STRUCTURED_SESSION` variables from child processes.
This prevents desktop hooks from attributing a background agent to its parent's
session. It does not change the parent environment, saved login, or global hooks.
Backend session history may still be visible in desktop apps that scan it.
Other environment variables are inherited except for the profile's `unset_env`
list. `doctor` verifies executable availability and runs the optional status probe;
without a probe it does not claim to have verified authentication or model access.

## Python integrations

Embedding applications may still pass any callable implementing `Agent` to `Harness`:

```python
def agent(role, context, cwd):
    # Invoke your runtime and return the schema-shaped proposal/review dictionary.
    ...
```

`CommandAgent` implements that protocol for one profile. `RoutedAgent` selects
profiles and models by role. `Harness` independently validates the returned object,
checks the worktree, and enforces approval gates regardless of transport.
