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
        system: pythonSets.${system}.mkVirtualEnv "lambda-harness-runtime" workspace.deps.default
      );
      development = forAllSystems (
        system: pythonSets.${system}.mkVirtualEnv "lambda-harness-development" workspace.deps.all
      );
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.writeShellApplication {
            name = "lambda-harness-web";
            runtimeInputs = [
              pkgs.git
              pkgs.gh
              pkgs.nix
              pkgs.cursor-cli
              pkgs.codex
              pkgs.claude-code
            ];
            text = ''exec ${runtimes.${system}}/bin/python -m lambda_harness "$@"'';
          };
        }
      );
      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/lambda-harness-web";
          meta.description = "Start the Lambda Harness local website";
        };
      });
      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = [
              development.${system}
              pkgs.git
              pkgs.gh
              pkgs.just
              pkgs.nixfmt
              pkgs.nodejs
              pkgs.prettier
              pkgs.uv
              pkgs.cursor-cli
              pkgs.codex
              pkgs.claude-code
            ];
            UV_PYTHON_DOWNLOADS = "never";
            UV_NO_SYNC = "1";
            shellHook = ''export PYTHONPATH="$PWD/src"'';
          };
        }
      );
      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          website = pkgs.runCommand "lambda-harness-website-check" { } ''
            ${self.packages.${system}.default}/bin/lambda-harness-web --help > help.txt
            ${runtimes.${system}}/bin/python -c 'from importlib.resources import files; root = files("lambda_harness").joinpath("web"); assert "Stop model" in root.joinpath("index.html").read_text(); assert "Project memory" in root.joinpath("guide.html").read_text(); assert "agent-grid" in root.joinpath("index.html").read_text(); assert files("lambda_harness").joinpath("agents", "rust-reviewer.json").is_file()'
            ${runtimes.${system}}/bin/python -c 'from importlib.metadata import distribution; assert not distribution("lambda-harness").entry_points'
            ${pkgs.codex}/bin/codex --ask-for-approval never exec --sandbox read-only --color never --ephemeral --ignore-user-config --ignore-rules - --help > codex-help.txt
            touch "$out"
          '';
          harness =
            pkgs.runCommand "lambda-harness-check"
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
                prettier --check src/lambda_harness/web tests/web.test.cjs
                node --check src/lambda_harness/web/app.js
                node --check src/lambda_harness/web/theme.js
                node --test tests/web.test.cjs
                pytest -q
                touch "$out"
              '';
        }
      );
      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
