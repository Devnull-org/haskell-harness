# Developing Haskell Harness

From a clone, Nix supplies the development tools:

```sh
nix develop
just lint
just test
just check
```

`just lint` checks Python, Nix, browser asset formatting, and JavaScript syntax.
`just test` runs the Python orchestration tests and Node frontend tests.
`just check` runs the flake checks, including the packaged website launcher.
These test the harness with temporary repositories and scripted agents, without
contacting models or running a target project's build.

Launch the development source with:

```sh
python -m haskell_harness --repo "/path/to/project"
```

Without Nix, use `uv run --locked pytest` and `uv run --locked ruff check .` for
Python checks. Node runs `node --test tests/web.test.cjs`. Nix formatting and
flake checks still require Nix. Run `uv lock` when changing dependencies.

The default Nix package includes the website launcher and supported backend CLIs.
There are no per-provider harness executables or terminal workflow commands.
`nix build` builds the runnable website package; `nix flake check` validates it.

The shared `Harness` workflow owns checkpoints, gates, validation, and worktrees.
The HTTP interface is in `webapp.py`, startup in `__main__.py`, and browser assets
in `web/`. Provider adapters are described in [BACKENDS.md](../BACKENDS.md).

- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [uv2nix](https://pyproject-nix.github.io/uv2nix/)
