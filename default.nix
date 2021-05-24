{ pkgs ? import <nixpkgs> { }
}:

with pkgs;
with lib;
rec {
  system = builtins.currentSystem;
  unstable = import
    (fetchTarball {
      url =
        "https://github.com/NixOS/nixpkgs/archive/6ed55034eec21f50c33afe7b4c4f5c13d49eba72.tar.gz";
      sha256 = "0594l26gmlc5s48hmrj37mq85hv9hbqvari3ckvzj0z4h4h8b50g";
    }) { };

  nixpkgs-review = import (fetchFromGitHub {
    owner = "rmcgibbo";
    repo = "nixpkgs-review";
    rev = "9f4884a8c292c144b8a29c7adc1c941ece6a12ed";
    sha256 = "1drk3hspampfcfz6x3h2dki15lkhyv5y9kjpvsknixccn13i2jk2";
  }) { };

  nixpkgs-hammer = (import (pkgs.fetchFromGitHub {
    owner = "jtojnar";
    repo = "nixpkgs-hammering";
    rev = "6a4f88d82ab7d0a95cf21494896bce40f7a4ac22";
    sha256 = "0597xl1q50ykwhvmnx30r2vvrlqfz8kpvr2zykcfj26rw69b8lh7";
  })).defaultPackage.${system};

  statx = import (pkgs.fetchFromGitHub {
    owner = "rmcgibbo";
    repo = "statx";
    rev = "ba90b5dd37fb1f5f01465015564e0a0aeb2cb5c3";
    sha256 = "0b0jrvas4rk4qvqn0pmw1v1ykzid6pzacrqmwkpn52azvmf904sr";
  }) { pkgs = pkgs; pythonPackages = python38.pkgs; };

  precedence-constrained-knapsack = unstable.callPackage (fetchFromGitHub {
    owner = "rmcgibbo";
    repo = "precedenceConstrainedKnapsack";
    rev = "fa4cc8556650acbaf74b7a75ab3e2b52bb3f44f7";
    sha256 = "0jzazpxg281xlswxdz0lhfwx4ivsckd85r92a0chyahh7rp1ygjg";
  }) { pkgs = unstable; python3Packages = unstable.python38Packages; };

  pyfst = unstable.python38Packages.callPackage (fetchFromGitHub {
    owner = "rmcgibbo";
    repo = "pyfst";
    rev = "f22b453fca8b83dd6a698fc30845a75d5d6c3cd7";
    sha256 = "sha256-xyZgQU/EOlbdlTOwGCZSRL7qzTAl6mhzyelIENkaXD8=";
  }) { };

  python-dynamodb-lock = with pkgs.python38Packages; buildPythonPackage rec {
    pname = "python-dynamodb-lock";
    version = "0.9.3";

    src = pkgs.fetchFromGitHub {
      owner = "whatnick";
      repo = "python_dynamodb_lock";
      rev = "v${version}";
      sha256 = "1jpn8mpxzx00cm9gm8z40rh0j0iw5akrm02qc8cd9v8z8dj7ysjf";
    };

    patches = [
      # Fixes compatibility of the test suite with python3.9
      (pkgs.fetchpatch {
        url = "https://github.com/rmcgibbo/python_dynamodb_lock/commit/35a77d79b4b8afc6d3947af3110de05be83e0c19.patch";
        sha256 = "0jfwd01vcgszqp4mml7rzsaxnns48j5n2cfphqr07f1cw0gbs41c";
      })
    ];

    propagatedBuildInputs = [ boto3 ];
    checkInputs = [ pytestCheckHook ];
    pythonImportsCheck = [ "python_dynamodb_lock" ];
  };

  nixbot-common = pkgs.python38.pkgs.callPackage ./nixbot-common { };

  nixbot-backend = pkgs.python38.pkgs.callPackage ./nixbot-backend {
    inherit nixbot-common;
    inherit nixpkgs-hammer;
    inherit nixpkgs-review;
    inherit precedence-constrained-knapsack;
    inherit statx;
    inherit pyfst;
    inherit python-dynamodb-lock;
  };
  nixbot-frontend = pkgs.python38.pkgs.callPackage ./nixbot-frontend {
    inherit nixbot-common;
    aiostream = unstable.python38Packages.aiostream;
  };
}
