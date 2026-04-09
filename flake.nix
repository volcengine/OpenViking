{
  description = "OpenViking service env from published wheel";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };
  };

  outputs = { nixpkgs, pyproject-nix, uv2nix, pyproject-build-systems, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };

      # This flake currently packages the published OpenViking wheel via the
      # tiny env project under nix/openviking-env. It does not package the
      # local checkout source tree.
      workspace = uv2nix.lib.workspace.loadWorkspace {
        workspaceRoot = ./nix/openviking-env;
      };

      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };

      pythonSet = (pkgs.callPackage pyproject-nix.build.packages {
        python = pkgs.python311;
      }).overrideScope (pkgs.lib.composeManyExtensions [
        pyproject-build-systems.overlays.default
        overlay
      ]);

      publishedOpenVikingServiceEnv = pythonSet.mkVirtualEnv
        "openviking-published-wheel-env"
        workspace.deps.default;
    in {
      packages.${system} = {
        default = publishedOpenVikingServiceEnv;
        openviking = publishedOpenVikingServiceEnv;
      };
    };
}
