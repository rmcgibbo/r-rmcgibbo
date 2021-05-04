from __future__ import annotations

import json
import os
import subprocess
import time
from typing import List, Optional

# from loguru import logger as log
from nixbot_common import configure_logging, isint

from .block_github_comment import is_blocked
from .github import GithubClient, determine_modified_files
from .nix import ReportJson
from .nixpkgs_hammer import nixpkgs_hammer
from .utils import with_distributed_lock

DEBUG = False


def main():
    """This hook gets called by nixpkgs-review in a nix-shell. In the current
    directory, there's
      * report.md
      * report.json
    """
    configure_logging()

    with open("report.json") as f:
        report_json: ReportJson = json.load(f)

    # Determine which files were patched so that we can post nixpkgs-hammer
    # suggestions only for drvs that are defined in files touched by this PR.
    gh = GithubClient(os.environ.get("GITHUB_TOKEN"))
    modified_files: Optional[List[str]] = None
    assert "PR" in os.environ and isint(os.environ["PR"])
    patchset = gh.load_patchset(int(os.environ["PR"]))
    modified_files = determine_modified_files(patchset)

    #
    # Determine which attrs were modified
    #
    with open("changed-attrs.json") as f:
        attrs = json.load(f)

    modified_attrs = []
    for name, v in attrs.items():
        if v["position"] is not None:
            position = os.path.relpath(v["position"].split(":")[0], "nixpkgs")
            if position in modified_files:
                modified_attrs.append(name)

    #
    # Run hammering, and append it to report.md
    #
    hammer_report, num_suggestions = nixpkgs_hammer(modified_attrs)
    report_json["hammer_report"] = hammer_report
    report_json["num_suggestions"] = num_suggestions
    if hammer_report is not None:
        with open("report.md", "a") as f:
            f.write(hammer_report)

    #
    # Attach a disclaimer if there are any failed tasks
    #
    if len(report_json["failed"]) > 0:
        with open("report.md", "a") as f:
            print(
                "\nNote that build failures [may predate](https://github.com/nix-community/hydra-check) "
                "this PR, and could be nondeterministic or hardware dependent.\n"
                "Please exercise your independent judgement.",
                file=f,
            )

    if not is_blocked(report_json):
        #
        # Finally, post result to github (while holding an app-wide distributed
        # lock so that only one person can post at a time).
        #
        with with_distributed_lock(True, os.environ["PR"]):
            subprocess.run(["nixpkgs-review", "post-result", "--prefer-edit"])
            time.sleep(2)  # give github some time to register the comment
        uploaded = True
    else:
        uploaded = False

    # Embellish report with some more information, so that we can upload to postgres
    report_json["uploaded"] = uploaded
    with open("report.json", "w") as f:
        json.dump(report_json, f)
