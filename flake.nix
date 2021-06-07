{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-21.05";
    utils.url = "github:numtide/flake-utils";
    nixpkgs-review = {
      url =
        "github:rmcgibbo/nixpkgs-review/b5bc2eac882a18f9f7f1e7acf34f8d55965aba92";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixpkgs-hammering = {
      url = "github:jtojnar/nixpkgs-hammering";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.utils.follows = "utils";
    };
    python-dynamodb-lock-src = {
      url =
        "github:rmcgibbo/python_dynamodb_lock/35a77d79b4b8afc6d3947af3110de05be83e0c19";
      flake = false;
    };
    precedence-constrained-knapsack = {
      url = "github:rmcgibbo/precedenceConstrainedKnapsack";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.utils.follows = "utils";
    };
    statx = {
      url = "github:rmcgibbo/statx";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.utils.follows = "utils";
    };
    pyfst = {
      url = "github:rmcgibbo/pyfst";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.utils.follows = "utils";
    };
  };

  outputs =
    { self
    , utils
    , nixpkgs
    , nixpkgs-review
    , nixpkgs-hammering
    , python-dynamodb-lock-src
    , statx
    , precedence-constrained-knapsack
    , pyfst
    }:
    utils.lib.eachSystem ["x86_64-linux" "aarch64-linux"] (system:
    let
      pkgs = import nixpkgs {
        inherit system;
        overlays = [
          statx.overlay
          nixpkgs-hammering.overlay
          precedence-constrained-knapsack.overlay
          pyfst.overlay
        ];
      };
      python-dynamodb-lock = with pkgs.python38Packages;
        buildPythonPackage rec {
          pname = "python-dynamodb-lock";
          version = "0.9.3";
          src = python-dynamodb-lock-src;
          propagatedBuildInputs = [ boto3 ];
          checkInputs = [ pytestCheckHook ];
          pythonImportsCheck = [ "python_dynamodb_lock" ];
        };
      nixbot-common = pkgs.python38.pkgs.callPackage ./nixbot-common { };
      nixbot-backend = pkgs.python38.pkgs.callPackage ./nixbot-backend {
        inherit nixbot-common;
        inherit python-dynamodb-lock;
        nixpkgs-review = nixpkgs-review.defaultPackage.${system};
      };
      nixbot-frontend = pkgs.python38.pkgs.callPackage ./nixbot-frontend {
        inherit nixbot-common;
      };

    in
    {
      packages = { inherit nixbot-frontend nixbot-backend; };
      defaultPackage = nixbot-backend;
      defaultApp = utils.lib.mkApp { drv = nixbot-backend; };
      nixosModules.r-rmcgibbo-frontend = { lib, pkgs, config, ... }:
        with lib;
        let cfg = config.services.r-rmcgibbo-frontend;
        in
        {
          options.services.r-rmcgibbo-frontend = {
            enable = mkEnableOption "r-rmcgibbo frontend";
            githubTokenScript = mkOption {
              type = types.str;
            };
            ec2Region = mkOption {
              type = types.str;
            };
            databaseUrlScript = mkOption {
              type = types.str;
            };
          };
          config = mkIf cfg.enable {
            users.users.r-rmcgibbo = {
              isNormalUser = true;
              initialHashedPassword = "";
              shell = pkgs.bash;
              extraGroups = [ "systemd-journal" ];
            };

            systemd.services.frontend = {
              enable = true;
              description = "r-rmcgibbo frontend";
              serviceConfig = {
                ExecStart = "/bin/sh -c 'export DATABASE_URL=$(${cfg.databaseUrlScript}); export GITHUB_TOKEN=$(${cfg.githubTokenScript}); exec ${pkgs.systemd}/bin/systemd-cat --priority info --stderr-priority err ${nixbot-frontend}/bin/nixbot-frontend'";
                User = "r-rmcgibbo";
                Restart = "on-failure";
                RestartSec = "5s";
              };
              environment.AWS_DEFAULT_REGION = cfg.ec2Region;
              environment.XDG_CACHE_HOME = "/home/r-rmcgibbo/.cache";
            };
          };
        };
      nixosModules.r-rmcgibbo-backend = { lib, pkgs, config, ... }:
        with lib;
        let cfg = config.services.r-rmcgibbo-backend;
        in
        {
          options.services.r-rmcgibbo-backend = {
            enable = mkEnableOption "r-rmcgibbo backend";
            githubTokenScript = mkOption {
              type = types.str;
            };
            ec2Region = mkOption {
              type = types.str;
            };
            databaseUrlScript = mkOption {
              type = types.str;
            };
          };
          config = mkIf cfg.enable {
            users.users.r-rmcgibbo = {
              isNormalUser = true;
              initialHashedPassword = "";
              shell = pkgs.bash;
              extraGroups = [ "systemd-journal" ];
            };

            systemd.services.nixpkgs-checkout = {
              wantedBy = [ "multi-user.target" ];

              serviceConfig = {
                User = "r-rmcgibbo";
                RemainAfterExit = true;
                Type = "oneshot";
                ExecStart = "${pkgs.git}/bin/git clone https://github.com/NixOS/nixpkgs.git /home/r-rmcgibbo/nixpkgs";
              };
            };
            systemd.services.backend = {
              enable = true;
              description = "r-rmcgibbo backend";
              serviceConfig = {
                ExecStart = "/bin/sh -c 'export DATABASE_URL=$(${cfg.databaseUrlScript}); export GITHUB_TOKEN=$(${cfg.githubTokenScript}); exec ${pkgs.systemd}/bin/systemd-cat --priority info --stderr-priority err ${nixbot-backend}/bin/nixbot-backend'";
                User = "r-rmcgibbo";
                Restart = "on-failure";
                RestartSec = "5s";
                WorkingDirectory = "/home/r-rmcgibbo/nixpkgs";
              };
              environment.AWS_DEFAULT_REGION = cfg.ec2Region;
              environment.XDG_CACHE_HOME = "/home/r-rmcgibbo/.cache";
            };
          };
        };
    });
}
