{ buildPythonApplication
, flit
, asyncpg
, aiodns
, aiostream
, aiohttp
, cchardet
, nixbot-common
, typing-extensions
, mypy
, black
, flake8
, pytest
, isort
, glibcLocales
}:

buildPythonApplication {
    pname = "nixbot-frontend";
    format = "pyproject";
    version = "0.1";

    src = ./.;

    nativeBuildInputs = [ flit ];
    propagatedBuildInputs = [
      aiodns
      cchardet
      aiohttp
      aiostream
      asyncpg
      nixbot-common
      typing-extensions
    ];
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
      # isort -df -rc --lines 999 src/
      echo -e "\x1b[32m## run black\x1b[0m"
      # LC_ALL=en_US.utf-8 black --check .
      echo -e "\x1b[32m## run flake8\x1b[0m"
      flake8 src --max-line-length 999 --ignore 'E203,W503'
      echo -e "\x1b[32m## run mypy\x1b[0m"
      mypy --check-untyped-defs --ignore-missing-imports src/
    '';
}