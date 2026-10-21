# Commands run inside `nix develop`.
default:
  @just --list

lint:
  ruff check .
  ruff format --check .
  nixfmt --check flake.nix
  prettier --check src/lambda_harness/web tests/web.test.cjs
  node --check src/lambda_harness/web/app.js
  node --check src/lambda_harness/web/theme.js

format:
  ruff format .
  nixfmt flake.nix
  prettier --write src/lambda_harness/web tests/web.test.cjs

test:
  node --test tests/web.test.cjs
  python -m pytest

check:
  nix flake check
