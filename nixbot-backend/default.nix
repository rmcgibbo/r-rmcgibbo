{ stdenv
, buildPythonApplication
, flit
, makeWrapper
, typing-extensions
, supervise_api
, unidiff
, statx
, nixbot-common
, python-dynamodb-lock
, mypy
, git
, black
, flake8
, pytest
, isort
, glibcLocales
, nixFlakes
, nixpkgs-review
, precedence-constrained-knapsack
, pyfst
, nixpkgs-hammer
, networkx
, ipython
, humanize
, coreutils
, cacert
, systemd
, awscli2
, psycopg2
}:

buildPythonApplication {
  pname = "nixbot-backend";
  format = "pyproject";
  version = "0.1";

  src = ./.;

  nativeBuildInputs = [ flit ];
  buildInputs = [ makeWrapper ];
  propagatedBuildInputs = [
    typing-extensions
    supervise_api
    nixbot-common
    unidiff
    networkx
    statx
    systemd
    humanize
    ipython
    pyfst
    precedence-constrained-knapsack
    python-dynamodb-lock
    psycopg2
  ];

  doCheck = true;
  checkInputs = [
    mypy
    black
    flake8
    pytest
    isort
    glibcLocales
  ];

  checkPhase = ''
    # echo -e "\x1b[32m## run unittest\x1b[0m"
    # py.test .
    echo -e "\x1b[32m## run isort\x1b[0m"
    isort -df -rc --lines 999 src/
    echo -e "\x1b[32m## run black\x1b[0m"
    # LC_ALL=en_US.utf-8 black --check .
    echo -e "\x1b[32m## run flake8\x1b[0m"
    flake8 src --max-line-length 999 --ignore 'E203,W503'
    echo -e "\x1b[32m## run mypy\x1b[0m"
    mypy --check-untyped-defs --ignore-missing-imports src/
  '';

  makeWrapperArgs = let
    binPath = stdenv.lib.makeBinPath ([
        nixpkgs-review
        git
        nixFlakes
        nixpkgs-hammer
        awscli2
        coreutils
      ]);
  in [
    "--prefix PATH : ${binPath}"
    "--set NIXPKGS_REVIEW_POST_BUILD_HOOK $out/bin/nixbot-backend-post-build-hook"
    "--set NIXPKGS_REVIEW_PRE_BUILD_FILTER $out/bin/nixbot-backend-pre-build-filter"
    "--set NIX_SSL_CERT_FILE ${cacert}/etc/ssl/certs/ca-bundle.crt"
  ];
}