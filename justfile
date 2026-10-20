# Commands run inside `nix develop`.
default:
  @just --list

lint:
  ruff check .
  ruff format --check .
  nixfmt --check flake.nix
  prettier --check src/haskell_harness/web
  node --check src/haskell_harness/web/app.js

format:
  ruff format .
  nixfmt flake.nix
  prettier --write src/haskell_harness/web

test:
  python -m pytest

check:
  nix flake check
