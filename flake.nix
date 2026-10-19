{
  description = "Local LangGraph and Cursor workflows for Hydra";

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
          config.allowUnfreePredicate = package: lib.getName package == "cursor-cli";
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
        system: pythonSets.${system}.mkVirtualEnv "hydra-harness-runtime" workspace.deps.default
      );
      development = forAllSystems (
        system: pythonSets.${system}.mkVirtualEnv "hydra-harness-development" workspace.deps.all
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
            name = "hydra-harness";
            runtimeInputs = [
              runtimes.${system}
              pkgs.git
              pkgs.nix
              pkgs.cursor-cli
            ];
            text = ''exec ${runtimes.${system}}/bin/hydra-harness "$@"'';
          };
        }
      );
      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/hydra-harness";
          meta.description = "Run a local Hydra development workflow";
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
              pkgs.just
              pkgs.nixfmt
              pkgs.uv
              pkgs.cursor-cli
            ];
            UV_PYTHON_DOWNLOADS = "never";
            UV_NO_SYNC = "1";
            shellHook = ''
              export PYTHONPATH="$PWD/src"
            '';
          };
        }
      );
      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          harness =
            pkgs.runCommand "hydra-harness-check"
              {
                nativeBuildInputs = [
                  development.${system}
                  pkgs.git
                  pkgs.nixfmt
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
                pytest -q
                touch "$out"
              '';
        }
      );
      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
