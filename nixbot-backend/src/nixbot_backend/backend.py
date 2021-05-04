from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.parse
from datetime import datetime, timedelta
from glob import glob
from typing import Dict, Iterator, List, Optional

import boto3
import psycopg2
import supervise_api
from loguru import logger as log
from nixbot_common import (
    configure_logging,
    create_nixpkgs_review_finished_table_sql,
    get_ec2_metadata,
)
from systemd.journal import sendv
from typing_extensions import TypedDict

SYSTEM = os.uname().machine
assert SYSTEM in ("aarch64", "x86_64")
IDLE_CUTOFF = timedelta(minutes=15)
# Note, this isn't exactly the idle time, because we don't pause the clock
# while we're working on builds. It's more like we exit & deprovision if the
# interval between SQS messages is greater than this. TODO: Probably should
# be changed to only count idle time?

# Maximum time for the nix-build step
BUILD_TIMEOUT = timedelta(hours=2)
# But it needs to produce output
SILENT_TIMEOUT = timedelta(hours=2)
# Give some extra time before killing the process
NIXPKGS_REVIEW_TIMEOUT = BUILD_TIMEOUT + timedelta(minutes=10)

SQSMessage = TypedDict(
    "SQSMessage",
    {
        "pr": int,
        "ofborg_url": Optional[str],
    },
)


def sh(cmd: List[str], timeout: Optional[float], env: Dict[str, str] = None) -> int:
    """Note that env is a set of _updates_ to the environment, not the complete
    environment!
    """
    # http://catern.com/posts/fork.html

    with supervise_api.Process(cmd, env=(env or {})) as proc:
        start_time = time.time()
        if timeout is None:
            return proc.wait()
        else:
            while time.time() - start_time <= timeout:
                try:
                    time.sleep(0.1)
                except RuntimeError:
                    subprocess.run(["git", "worktree", "prune"])
                    raise
                returncode = proc.poll()
                if returncode is not None:
                    return returncode
            log.error("BUILD TIMED OUT!")
            return -1  # indicate timeout


def env_with(**kwargs: str) -> Dict[str, str]:
    env = {}
    for key, value in kwargs.items():
        if "$PATH" in value:
            value = value.replace("$PATH", os.environ["PATH"])
        env[key] = value

    return env


def build_pr(
    database_url: Optional[str], pr: int, ofborg_url: Optional[str] = None
) -> None:
    log.info("Starting build", pr=pr)
    for dirname in glob(os.path.expanduser(f"~/.cache/nixpkgs-review/pr-{pr}*")):
        shutil.rmtree(dirname)

    if ofborg_url is not None:
        url = urllib.parse.urlparse(ofborg_url)
        raw_gist_url = (
            f"https://gist.githubusercontent.com/GrahamcOfBorg{url.path}/raw/"
        )
        os.environ["NIXPKGS_REVIEW_OFBORG_GIST_URL"] = raw_gist_url

    # This is important
    # We need to send a message to the journald logs with the right format
    # so that post-build-postgres knows what PR we're working on
    # https://github.com/rmcgibbo/post-build-postgres/blob/master/src/journal.rs#L18
    sendv(
        f"MESSAGE={json.dumps({'pr': pr})}",
        "PRIORITY=6",
        "SYSLOG_IDENTIFIER=nixpkgs-review-start",
    )

    cmd = [
        # Call the unwrapped version so that we can override its PATH and hijack
        # nix-shell to point to our own shell script. The reason for this is because
        # actually running nix-shell can force a bunch more stuff to be built, because
        # it runs nix-shell with the nixpkgs from the PR, and so if the PR rebuilt bash
        # then you're going to need to build bash _EVEN IF_ it was skipped.
        ".nixpkgs-review-wrapped",
        "pr",
        str(pr),
        "--post-logs",
        # TODO: pass --system here
        "--build-args",
        f"--timeout {int(BUILD_TIMEOUT.total_seconds())} --max-silent-time {int(SILENT_TIMEOUT.total_seconds())}",
        "--run",
        os.environ["NIXPKGS_REVIEW_POST_BUILD_HOOK"],
    ]
    if "GITHUB_TOKEN" not in os.environ:
        log.error("No GITHUB_TOKEN. Proceeding without --post-logs")
        cmd.remove("--post-logs")

    start_time = time.time()
    sh(
        cmd,
        timeout=NIXPKGS_REVIEW_TIMEOUT.total_seconds(),
        env=env_with(
            NIXPKGS_REVIEW_START_TIME=f"{start_time}",
            NIXPKGS_REVIEW_PR=f"{pr}",
            PATH=f"{os.path.join(os.path.dirname(__file__), 'bin')}:$PATH",
        ),
    )

    nixpkgs_dir = os.path.expanduser(f"~/.cache/nixpkgs-review/pr-{pr}/nixpkgs")
    if os.path.exists(nixpkgs_dir):
        shutil.rmtree(nixpkgs_dir)
    upload_s3(pr=pr, start_time=start_time)
    upload_postgres(pr=pr, start_time=start_time, database_url=database_url)


def upload_postgres(pr: int, start_time: float, database_url: Optional[str]) -> None:
    if database_url is None:
        return

    file_name = os.path.expanduser(f"~/.cache/nixpkgs-review/pr-{pr}/report.json")
    try:
        with open(file_name) as f:
            report = json.load(f)
        report_json_str = json.dumps(
            {
                k: len(v) if isinstance(v, list) else v
                for k, v in report.items()
                if k not in ("hammer_report",)
            }
        )
        state = "success"
    except FileNotFoundError:
        log.error("File does not exist", file_name=file_name)
        report_json_str = "null"
        state = "crashed"

    metadata = get_ec2_metadata()
    conn = psycopg2.connect(database_url)

    SQL = """
        INSERT INTO nixpkgs_review_finished(
            build_elapsed,
            ctime,
            pull_request_number,
            state,
            system,
            instance_type,
            instance_id,
            report
        ) VALUES (make_interval(secs => %s), %s, %s, %s, %s, %s, %s, %s)
    """

    with conn.cursor() as curs:
        curs.execute(create_nixpkgs_review_finished_table_sql())
        curs.execute(
            SQL,
            (
                (time.time() - start_time),
                datetime.now().astimezone(),
                pr,
                state,
                SYSTEM,
                (metadata["instanceType"] if metadata is not None else ""),
                (metadata["instanceId"] if metadata is not None else ""),
                report_json_str,
            ),
        )

    conn.commit()


def upload_s3(pr: int, start_time: float) -> None:
    file_name = os.path.expanduser(f"~/.cache/nixpkgs-review/pr-{pr}/report.md")
    if not os.path.exists(file_name):
        log.error("File does not exist", file_name=file_name)
        return

    bucket = "nixpkgs-review-bot"
    object_name = f"pr-results/{SYSTEM}/{pr}.md"

    with open(file_name) as f:
        report = f.read()

    if "NIXPKGS_REVIEW_DRY_RUN" not in os.environ:
        s3 = boto3.client("s3")
        s3.upload_file(file_name, bucket, object_name)
    log.info("Finished build", pr=pr, report=report)


def get_from_sqs() -> List:
    sqs = boto3.resource("sqs")
    queue = sqs.get_queue_by_name(QueueName=f"nixpkgs-buildbot-{SYSTEM}")
    return queue.receive_messages(MaxNumberOfMessages=1, WaitTimeSeconds=20)


def deprovision_backend() -> None:
    metadata = get_ec2_metadata()

    cmd = [
        "aws",
        "autoscaling",
        "terminate-instance-in-auto-scaling-group",
        "--instance-id",
        metadata["instanceId"],
        "--should-decrement-desired-capacity",
    ]
    subprocess.run(cmd, check=True, text=True)


def iter_sqs() -> Iterator[SQSMessage]:
    last_sqs_message = time.time()
    while True:
        messages = get_from_sqs()
        log.info("Polled SQS", count=len(messages), messages=messages)
        if len(messages) > 0:
            last_sqs_message = time.time()

        if (
            len(messages) == 0
            and last_sqs_message is not None
            and time.time() - last_sqs_message > IDLE_CUTOFF.total_seconds()
        ):
            deprovision_backend()
            # don't return here. we might just die waiting, or maybe
            # there's a race condition and another side of the system
            # will increase the autoscaling group count

        for m in messages:
            try:
                body = json.loads(m.body)
            finally:
                # Let the queue know that the message is processed.
                # If the build times out or crashes or this host gets killed
                # we don't want the message to return to the queue.
                # NOTE: we could re-consider this and give it multiple retries
                # or something?
                m.delete()
            log.info("processing message", body=body)
            # Mar 28, 12:21 AM backend ec2-184-73-139-153.compute-1.amazonaws.com x86_64 processing message | body={'pr': 117861, 'ofborg_url': 'https://gist.github.com/65f95a461f306e5e14c3e61abcf57c1d'}

            yield body


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--seed-prs", type=int, action="append", default=[])
    p.add_argument("--dry-run", default=False, action="store_true")
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = p.parse_args()

    if "GITHUB_TOKEN" not in os.environ and not args.dry_run:
        log.error("No GITHUB_TOKEN env var. Setting --dry-run")
        args.dry_run = True

    if args.dry_run:
        os.environ["NIXPKGS_REVIEW_DRY_RUN"] = "1"

    def source() -> Iterator[SQSMessage]:
        for pr in args.seed_prs:
            yield {"pr": pr, "ofborg_url": None}
        if args.dry_run:
            return
        yield from iter_sqs()

    for msg in source():
        assert "database_url" not in msg
        build_pr(database_url=args.database_url, **msg)
        if not args.dry_run:
            subprocess.run(
                ["nix-collect-garbage", "-d"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    log.info("Finished")
