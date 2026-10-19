# Commands run inside `nix develop`.
default:
  @just --list

lint:
  ruff check .
  ruff format --check .
  nixfmt --check flake.nix

format:
  ruff format .
  nixfmt flake.nix

test:
  python -m pytest

check:
  nix flake check
