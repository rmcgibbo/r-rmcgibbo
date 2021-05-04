from __future__ import annotations

import json
import os
import shutil
from itertools import chain
from typing import Any, Dict

from loguru import logger as log

from .github import GithubClient
from .nix import ReportJson
from .utils import journald_logs_since

# Users have requested not to receive build conformations. If the PR
# is from one of these users, we only want to post if there's a build
# failure. These are github usernames.
# Source: https://github.com/SuperSandro2000/nixpkgs-review-checks/blob/master/bashrc#L67
GH_USER_BLOCKLIST = {
    "alyssais",
    "ashkitten",
    "andir",
    "edef1c",
    "mweinelt",
    "adisbladis",
    "NinjaTrappeur",
    "vbgl",
}


def upload_blocked_due_to_blocklist(pr_user_login: str, rj: ReportJson) -> bool:
    if (pr_user_login in GH_USER_BLOCKLIST) and len(rj["failed"]) == 0:
        return True
    return False


def upload_blocked_oom_or_enospc():
    if "NIXPKGS_REVIEW_START_TIME" not in os.environ:
        log.error("NIXPKGS_REVIEW_START_TIME not set")
        return False

    def try_decode(entry: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return json.loads(entry["MESSAGE"])
        except Exception:
            return {}

    start_time = float(os.environ["NIXPKGS_REVIEW_START_TIME"])
    journal = journald_logs_since(
        dict(_SYSTEMD_UNIT="oom-enospc-notify.service"), start_time=start_time
    )

    messages = [try_decode(entry) for entry in journal]
    if any(msg.get("event") in ("OOM Kill", "ENOSPC") for msg in messages):
        return True
    return False


def upload_blocked_earlyoom():
    # Mar 14 22:44:43 ip-10-0-15-194.ec2.internal systemd[1]: Started Early OOM killer.
    # Mar 14 22:44:43 ip-10-0-15-194.ec2.internal earlyoom[1024]: earlyoom 1.6.1
    # Mar 14 22:44:43 ip-10-0-15-194.ec2.internal earlyoom[1024]: Priority was raised successfully
    # Mar 14 22:44:43 ip-10-0-15-194.ec2.internal earlyoom[1024]: mem total: 3838 MiB, swap total:    0 MiB
    # Mar 14 22:44:43 ip-10-0-15-194.ec2.internal earlyoom[1024]: sending SIGTERM when mem <= 10.00% and swap <= 100.00%,
    # Mar 14 22:44:43 ip-10-0-15-194.ec2.internal earlyoom[1024]:         SIGKILL when mem <=  5.00% and swap <= 50.00%
    # Mar 14 22:52:28 ip-10-0-15-194.ec2.internal earlyoom[1024]: mem avail:   381 of  3838 MiB ( 9.94%), swap free:    0 of    0 MiB ( 0.00%)
    # Mar 14 22:52:28 ip-10-0-15-194.ec2.internal earlyoom[1024]: low memory! at or below SIGTERM limits: mem 10.00%, swap 100.00%
    # Mar 14 22:52:28 ip-10-0-15-194.ec2.internal earlyoom[1024]: sending SIGTERM to process 2265 uid 0 "nix-env": badness 1201, VmRSS 3074 MiB
    # Mar 14 22:52:29 ip-10-0-15-194.ec2.internal earlyoom[1024]: process exited after 1.0 seconds

    # Mar 15 03:03:06 ip-10-0-13-27.ec2.internal earlyoom[1027]: mem avail:   377 of  3838 MiB ( 9.84%), swap free:    0 of    0 MiB ( 0.00%)
    # Mar 15 03:03:06 ip-10-0-13-27.ec2.internal earlyoom[1027]: low memory! at or below SIGTERM limits: mem 10.00%, swap 100.00%
    # Mar 15 03:03:06 ip-10-0-13-27.ec2.internal earlyoom[1027]: sending SIGTERM to process 11827 uid 30001 "rustc": badness 949, VmRSS 1627 MiB
    # Mar 15 03:03:06 ip-10-0-13-27.ec2.internal earlyoom[1027]: process exited after 0.1 seconds

    if "NIXPKGS_REVIEW_START_TIME" not in os.environ:
        log.error("NIXPKGS_REVIEW_START_TIME not set")
        return False

    start_time = float(os.environ["NIXPKGS_REVIEW_START_TIME"])
    journal = journald_logs_since(
        dict(_SYSTEMD_UNIT="earlyoom.service"), start_time=start_time
    )

    def is_kill(entry: Dict[str, Any]) -> bool:
        c1 = "sending SIGTERM to process" in entry["MESSAGE"]
        c2 = "sending SIGKILL to process" in entry["MESSAGE"]
        if c1 or c2:
            print(f"Entry: {entry}")
            return True
        return False

    if any(is_kill(entry) for entry in journal):
        return True
    return False


def upload_blocked_single_build(rj: ReportJson):
    is_single_clean_attr = (
        len(rj["built"]) == 1 and len(rj["failed"]) == 0 and rj["hammer_report"] is None
    )
    if is_single_clean_attr:
        return True
    return False


def upload_blocked_disk_full(rj: ReportJson) -> bool:
    disk = shutil.disk_usage("/nix")
    if len(rj["failed"]) > 0 and (disk.used / disk.total > 0.95):
        return True
    return False


def upload_blocked_empty(rj: ReportJson) -> bool:
    if len(rj["built"]) + len(rj["failed"]) == 0:
        return True
    return False


def upload_blocked_timed_out(rj: ReportJson) -> bool:
    if len(rj["timed_out"]) > 0:
        return True
    return False


def upload_blocked_due_to_pr_closed(pr_data: Dict[str, Any]) -> bool:
    if pr_data["state"] == "closed":
        return True
    return False


def github_comment_is_nixpkgs_review(comment: Dict[str, Any]) -> bool:
    uname = os.uname()
    system = f"{uname.machine}-{uname.sysname.lower()}"

    NEEDLE = f"run on {system} [1](https://github.com/Mic92/nixpkgs-review)"
    return NEEDLE in comment["body"]


def github_comment_is_editable(comment: Dict[str, Any]) -> bool:
    try:
        return comment["user"]["login"] == "r-rmcgibbo"
    except KeyError:
        return False


def is_blocked(report_json: ReportJson) -> bool:
    gh = GithubClient(os.environ.get("GITHUB_TOKEN"))

    if upload_blocked_oom_or_enospc():
        report_json["blocked_reason"] = "OOM_ENOSPC"
        log.error("Upload blocked because I think there was an OOM or ENOSPC")
        return True

    if upload_blocked_earlyoom():
        report_json["blocked_reason"] = "EARLY_OOM"
        log.error("Upload blocked because I think there was an Early OOM")
        return True

    if upload_blocked_disk_full(report_json):
        report_json["blocked_reason"] = "DISK_FULL"
        log.error("Upload blocked because I think the disk is full?")
        return True

    if upload_blocked_empty(report_json):
        report_json["blocked_reason"] = "NO_PACKAGES_BUILT"
        log.error("Upload blocked because no packages were built")
        return True

    if upload_blocked_timed_out(report_json):
        report_json["blocked_reason"] = "BUILD_TIMEOUT"
        log.error("Upload blocked because there was a timeout")
        return True

    prev_gh_comments = gh.pull_request_comments(report_json["pr"])
    is_second_build = any(github_comment_is_editable(c) for c in prev_gh_comments)
    is_first_build = not is_second_build

    if is_first_build:
        #
        # These conditions only apply if we're considering making our first comment
        # on this issue. These reasons for blocking a comment upload are related to
        # not wanting to cause an extra notification. So they don't apply if we're
        # just going to edit a prior comment, which does not trigger an extra
        # notification.
        #
        if upload_blocked_single_build(report_json):
            report_json["blocked_reason"] = "SINGLE_CLEAN_PACKAGE"
            log.error("Upload blocked because PR contains 1 clean package")
            return True

        #
        # Determine if the person who posted the PR has opted out from receiving
        # notifications with no build failures
        #
        pr_data = gh.pull_request(report_json["pr"])
        if upload_blocked_due_to_blocklist(
            pr_data["user"]["login"], report_json
        ):
            report_json["blocked_reason"] = "AUTHOR_BLOCKLIST_CLEAN"
            log.error("Upload blocked because user in blocklist and no build failures")
            return True

        if upload_blocked_due_to_pr_closed(pr_data):
            report_json["blocked_reason"] = "PR_CLOSED"
            log.error("Upload blocked because PR is closed/merged")
            return True

        #
        # Determine if we should not upload because someone else has already uploaded
        # substantially the same comment
        #
        if (
            any(
                github_comment_is_nixpkgs_review(c)
                for c in chain([pr_data], prev_gh_comments)
            )
            and len(report_json["failed"]) == 0
            and (report_json["hammer_report"] is None)
        ):
            report_json["blocked_reason"] = "PREVIOUS_REVIEW"
            log.error("Upload blocked because previous nixpkgs-review")
            return True

    if "NIXPKGS_REVIEW_DRY_RUN" in os.environ:
        report_json["blocked_reason"] = "DRY_RUN"
        log.error("Upload blocked because DRY_RUN")
        return True

    log.error("Upload not blocked for any reason")
    return False
