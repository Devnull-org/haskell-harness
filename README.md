<div align="center">

# λ Lambda Harness

### Build with agents. Keep the decisions yours.

A local workspace for functional programming teams, from conversation to reviewed patches.

[Get started](#open-your-workspace) · [User guide](docs/GUIDE.md) · [Model backends](BACKENDS.md) · [Contribute](docs/DEVELOPMENT.md)

</div>

---

Good software starts with a clear conversation. Lambda Harness helps you explore
an idea, sharpen the requirements, and work through the change with specialist
agents—all from a local website.

**You approve the specification. You approve the tests. You choose what happens next.**

## A workflow that respects your craft

- **Think before coding.** Explore the repository and compare approaches, or let an
  interviewer challenge your assumptions until the requirements are clear.
- **Tests lead the way.** Review failing Cabal tests before implementation begins.
  Strict builds, lint, and Nix checks accompany code changes.
- **Get a second opinion.** Dedicated Haskell and security reviewers examine the
  implementation, or review an existing PR. An optional walkthrough helps you retain context.
- **Keep project knowledge close.** Read and edit shared memory, preview what agents
  receive, and revisit earlier versions from the website.
- **Own every patch.** Work stays in isolated local worktrees, with saved conversations
  and small commits. Prepare a handoff when you're ready to publish.

Start where you need help: have a chat, explore an idea, write tests, review a PR,
or take a feature through the full workflow. Every phase is a choice.

```mermaid
flowchart LR
  Explore --> Interview
  Interview --> Spec[Approve specification]
  Spec --> Tests[Failing tests]
  Tests --> Approval[Approve tests]
  Approval --> Implement
  Implement --> Refactor
  Refactor --> Review
  Review --> Handoff[Local handoff]
```

## Open your workspace

With Nix, run this from any directory:

```sh
nix run --refresh github:Devnull-org/lambda-harness -- --repo "/path/to/project"
```

Replace the path with your Git checkout, then open the printed local URL.
The command starts the local server; keep it running while using the website.

Choose **General chat**, **Explore**, **Plan a feature or fix**,
**Review a PR or branch**, or **Write tests**.
Describe what you need. The harness checks your existing model logins and lets you
choose when more than one backend is available.

**Light, dark, or your system theme.** A familiar editor, visible conversation
history, and a stop button when you want to change direction.

Prefer running without Nix? See the [user guide](docs/GUIDE.md#without-nix).

## Your project. Your tools.

Powered by LangGraph and independent of any one model provider. The coding workflow
currently supports Haskell and Nix; more functional languages are planned.
Use existing agent logins or connect your own backend. Save architecture
notes and invariants as shared project context for the team’s workflow.

The website runs locally. Model requests use your chosen provider. The harness
creates no pull requests and pushes no code automatically.

[Explore the workflow →](docs/GUIDE.md) · [Configure project checks →](docs/VALIDATION.md)
