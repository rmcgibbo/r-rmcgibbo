import json
import os
import subprocess
from functools import lru_cache
from itertools import islice
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger as log

# https://github.com/rmcgibbo/nixpkgs-review-bot/issues/68
MAX_SUGGESTIONS_PER_PACKAGE = 20

# https://github.com/rmcgibbo/nixpkgs-review-bot/issues/26
IGNORE_SUGGESTIONS_FOR_FILES = {
    "pkgs/misc/vim-plugins/generated.nix",
    "pkgs/misc/vim-plugins/overrides.nix",
    "pkgs/misc/tmux-plugins/default.nix",
}

# https://github.com/jtojnar/nixpkgs-hammering/issues/73
# https://github.com/jtojnar/nixpkgs-hammering/issues/77#issuecomment-786193493
# https://github.com/jtojnar/nixpkgs-hammering/pull/78#pullrequestreview-599072677
# https://github.com/jtojnar/nixpkgs-hammering/issues/73#issuecomment-817819413
ATTRS_THAT_BREAK_NIXPKGS_HAMMER = {
    "acl",
    "attr",
    "bash",
    "binutils-unwrapped",
    "bzip2",
    "coreutils",
    "coreutils-full",
    "coreutils-prefixed",
    "datadog-agent",
    "diffutils",
    "findutils",
    "gawkInteractive",
    "gcc-unwrapped",
    "gccForLibs",
    "glibc",
    "gnugrep",
    "gnupatch",
    "gnused",
    "gnutar",
    "gzip",
    "holochain-go",
    "javaPackages.junit_4_12",
    "javaPackages.mavenHello_1_0",
    "javaPackages.mavenHello_1_1",
    "libgccjit",
    "zfsbackup",
}


@lru_cache()
def _get_nixpkgs() -> str:
    assert os.path.exists("nixpkgs")
    return "nixpkgs"


def nixpkgs_hammer(attrs: List[str]) -> Tuple[Optional[str], int]:
    """Run nixpkgs-hammer on each attr, saves the results into the
    'check_report' field on the attr.

    Only save checks if the 'location' of the bug flagged by the check is within
    the set of files modified by the pr, which should be passed in `modified_files`.
    """

    attrs_to_hammer = [a for a in attrs if a not in ATTRS_THAT_BREAK_NIXPKGS_HAMMER]
    if len(attrs_to_hammer) == 0:
        return None, 0

    cmd = [
        "nixpkgs-hammer",
        "-f",
        _get_nixpkgs(),
        "--json",
        "--exclude",
        "attribute-ordering",
        "--exclude",
        "explicit-phases",
        # Discussion here: https://github.com/jtojnar/nixpkgs-hammering/pull/38#issuecomment-778381992
        "--exclude",
        "attribute-typo",
        "--exclude",
        "no-build-output",
    ] + attrs_to_hammer

    log.info("$ " + " ".join(cmd))

    env = os.environ.copy()
    env["NIXPKGS_ALLOW_UNSUPPORTED_SYSTEM"] = "1"
    env["NIXPKGS_ALLOW_BROKEN"] = "1"
    env["NIXPKGS_ALLOW_UNFREE"] = "1"

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as e:
        log.error("nixpkgs-hammer crashed")
        log.error(os.path.abspath(os.getcwd()))
        log.error(e.stderr)
        log.error(e.stderr)
        log.error(e.returncode)
        return None, 0

    hammer_report = json.loads(proc.stdout)

    #
    # Replace `file` with relative path -- should upstream this into
    # nixpkgs-hammering
    #
    for name, data in hammer_report.items():
        for msg in data:
            for location in msg.get("locations", []):
                if "file" in location and isinstance(location["file"], str):
                    location["file"] = os.path.relpath(location["file"], _get_nixpkgs())

    check_reports = set()
    for name, data in hammer_report.items():
        for msg in islice((m for m in data if is_acceptable_hammer_message(m)), MAX_SUGGESTIONS_PER_PACKAGE):
            check_reports.add(stringify_message(**msg))

    if len(check_reports) == 0:
        return None, 0
    return html_check_reports(list(check_reports)), len(check_reports)


def html_check_reports(check_reports: List[str]) -> str:
    if len(check_reports) == 0:
        return ""
    plural = "s" if len(check_reports) > 1 else ""
    res = "<details>\n"
    res += f"  <summary>{len(check_reports)} suggestion{plural}:</summary>\n  <ul>\n"
    for report in check_reports:
        res += f"    <li>{report}"
        res += "</li>\n"
    res += "  </ul>\n</details>\n"
    return res


def stringify_location(file: str, line: int, column: Optional[int]):
    if column is None:
        column = 0
    with open(os.path.join(_get_nixpkgs(), file), "r") as opened_file:
        all_lines = opened_file.read().splitlines()
        line_contents = all_lines[line - 1]
        line_spaces = " " * len(str(line))
        pointer = " " * (column - 1) + "^"

        location_lines = [
            "Near " + file + ":" + str(line) + ":" + str(column) + ":",
            "```",
            line_spaces + " |",
            str(line) + " | " + line_contents,
            line_spaces + " | " + pointer,
            "```",
            "",
        ]

    return "\n".join(location_lines)


def stringify_message(
    name: str,
    msg: str,
    locations: List[Dict[str, Any]] = [],
    cond: bool = True,
    link: bool = True,
    severity: str = "warning",
) -> str:
    if link:
        linked_name = f'<a href="https://github.com/jtojnar/nixpkgs-hammering/blob/master/explanations/{name}.md">{name}</a>'
    else:
        linked_name = name

    message_lines = [
        f"{severity}: {linked_name}",
        "",
        msg,
    ] + list(map(lambda loc: stringify_location(**loc), locations))

    return "\n".join(message_lines)


def is_acceptable_hammer_message(msg: Dict[str, Any]) -> bool:
    predicates = [
        lambda: not is_ignored_file(msg),
        lambda: not is_ignored_golang_buildFlagsArray_msg(msg),
        lambda: msg["name"] not in ("no-build-output", "EvalError", "AttrPathNotFound"),
    ]
    return all(pred() for pred in predicates)


def is_ignored_file(msg: Dict[str, Any]) -> bool:
    return any(
        loc["file"] in IGNORE_SUGGESTIONS_FOR_FILES for loc in msg.get("locations", [])
    )


def is_ignored_golang_buildFlagsArray_msg(msg: Dict[str, Any]) -> bool:
    for loc in msg["locations"]:
        is_golang = False
        with open(os.path.join(_get_nixpkgs(), loc["file"])) as f:
            content = f.read()
            is_golang = "buildGoModule" in content or "buildGoPackage" in content

        if is_golang and msg["name"] == "no-flags-array":
            return True

    return False
