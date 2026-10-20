{
  description = "Local LangGraph workflows with pluggable model backends";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    inputs:
    let
      inherit (inputs)
        self
        nixpkgs
        pyproject-nix
        uv2nix
        pyproject-build-systems
        ;
      inherit (nixpkgs) lib;
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      forAllSystems = lib.genAttrs systems;
      pkgsFor =
        system:
        import nixpkgs {
          inherit system;
          config.allowUnfreePredicate =
            package:
            builtins.elem (lib.getName package) [
              "cursor-cli"
              "claude-code"
            ];
        };
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
      pythonSets = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python313; }).overrideScope (
          lib.composeManyExtensions [
            pyproject-build-systems.overlays.wheel
            overlay
          ]
        )
      );
      runtimes = forAllSystems (
        system: pythonSets.${system}.mkVirtualEnv "haskell-harness-runtime" workspace.deps.default
      );
      development = forAllSystems (
        system: pythonSets.${system}.mkVirtualEnv "haskell-harness-development" workspace.deps.all
      );
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          mkHarness =
            extra: backend:
            let
              launcher = pkgs.writeShellApplication {
                name = "haskell-harness";
                runtimeInputs = [
                  runtimes.${system}
                  pkgs.git
                  pkgs.nix
                ]
                ++ extra;
                text = ''exec ${runtimes.${system}}/bin/haskell-harness ${
                  lib.optionalString (backend != null) "--backend ${lib.escapeShellArg backend}"
                } "$@"'';
              };
            in
            pkgs.symlinkJoin {
              name = "haskell-harness";
              paths = [ launcher ];
              meta.mainProgram = "haskell-harness";
            };
        in
        {
          default = mkHarness [
            pkgs.cursor-cli
            pkgs.codex
            pkgs.claude-code
          ] null;
          local = mkHarness [ pkgs.cursor-cli ] "local";
          codex = mkHarness [ pkgs.codex ] "codex";
          claude-code = mkHarness [ pkgs.claude-code ] "claude-code";
        }
      );
      apps = forAllSystems (
        system:
        lib.genAttrs [ "default" "local" "codex" "claude-code" ] (name: {
          type = "app";
          program = "${self.packages.${system}.${name}}/bin/haskell-harness";
          meta.description = "Run a local development workflow";
        })
      );
      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          mkDevelopment =
            extra:
            pkgs.mkShell {
              packages = [
                development.${system}
                pkgs.git
                pkgs.just
                pkgs.nixfmt
                pkgs.nodejs
                pkgs.prettier
                pkgs.uv
              ]
              ++ extra;
              UV_PYTHON_DOWNLOADS = "never";
              UV_NO_SYNC = "1";
              shellHook = ''
                export PYTHONPATH="$PWD/src"
              '';
            };
        in
        {
          default = mkDevelopment [
            pkgs.cursor-cli
            pkgs.codex
            pkgs.claude-code
          ];
          local = mkDevelopment [ pkgs.cursor-cli ];
          codex = mkDevelopment [ pkgs.codex ];
          claude-code = mkDevelopment [ pkgs.claude-code ];
        }
      );
      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          commands = pkgs.runCommand "haskell-harness-commands-check" { } ''
            ${self.packages.${system}.default}/bin/haskell-harness help > help.txt
            ${self.packages.${system}.default}/bin/haskell-harness serve --help > serve-help.txt
            ${runtimes.${system}}/bin/python -c 'from importlib.resources import files; assert "Stop model" in files("haskell_harness").joinpath("web", "index.html").read_text()'
            ${self.packages.${system}.default}/bin/haskell-harness --json backends > auto-backends.json
            ${runtimes.${system}}/bin/python -c 'import json; assert json.load(open("auto-backends.json"))["default"] == "auto"'
            ${self.packages.${system}.codex}/bin/haskell-harness --json backends > codex-backends.json
            ${runtimes.${system}}/bin/python -c 'import json; assert json.load(open("codex-backends.json"))["default"] == "codex"'
            ${pkgs.codex}/bin/codex --ask-for-approval never exec --sandbox read-only --color never --ephemeral --ignore-user-config --ignore-rules - --help > codex-help.txt
            touch "$out"
          '';
          harness =
            pkgs.runCommand "haskell-harness-check"
              {
                nativeBuildInputs = [
                  development.${system}
                  pkgs.git
                  pkgs.nixfmt
                  pkgs.nodejs
                  pkgs.prettier
                ];
              }
              ''
                export HOME="$TMPDIR/home"
                mkdir -p "$HOME"
                cp -r ${./.} source
                chmod -R u+w source
                cd source
                export PYTHONPATH="$PWD/src"
                ruff check .
                ruff format --check .
                nixfmt --check flake.nix
                prettier --check src/haskell_harness/web
                node --check src/haskell_harness/web/app.js
                pytest -q
                touch "$out"
              '';
        }
      );
      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
