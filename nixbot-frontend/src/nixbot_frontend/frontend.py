from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from functools import partial
from typing import Any, Dict, List, Optional

import aiohttp
import asyncpg
import boto3
from aiostream import stream
from loguru import logger as log
from nixbot_common import configure_logging, create_nixpkgs_review_dispatched_table_sql

from .nixpkgs import aiter_nixpkgs_events, event_is_pull_request_opened, get_ofborg_eval, pr_number_as_pull_event
from .server import aiter_server_events

SQSQueue = Any
BUILD_SERVER_ASG = {
    "aarch64-linux": "backend-aarch64",
    "x86_64-linux": "backend-x86_64",
}
SQS_QUEUE_NAMES = {
    "aarch64-linux": "nixpkgs-buildbot-aarch64",
    "x86_64-linux": "nixpkgs-buildbot-x86_64",
}
assert sorted(BUILD_SERVER_ASG.keys()) == sorted(SQS_QUEUE_NAMES.keys())
ALL_BUILD_SYSTEMS = set(BUILD_SERVER_ASG.keys())


def get_sqs() -> Dict[str, SQSQueue]:
    sqs = boto3.resource("sqs")
    return {
        system: sqs.get_queue_by_name(QueueName=queuename)
        for system, queuename in SQS_QUEUE_NAMES.items()
    }


def get_autoscaling():
    client = boto3.client("autoscaling")
    return client


async def log_buildable_pr(
    conn: Optional[asyncpg.Connection], pr: int, ofborg_eval: Dict[str, Any]
) -> None:
    if conn is None:
        return

    SQL = """
        INSERT INTO nixpkgs_review_dispatched (
            pull_request_number,
            state,
            ofborg_eval_url,
            num_packages,
            ctime
        )
        VALUES($1, $2, $3, $4, $5)
    """
    args = [
        pr,
        "dispatched",
        ofborg_eval["url"],
        json.dumps({k: len(v) for k, v in ofborg_eval["packages_per_system"].items()}),
        datetime.now().astimezone(),
    ]
    await conn.execute(SQL, *args)


async def aiter_opened_prs(seed_prs, session):
    for pr in seed_prs:
        event = await pr_number_as_pull_event(pr, session)
        if event_is_pull_request_opened(event):
            yield event

    async with stream.merge(
        aiter_nixpkgs_events(session), aiter_server_events(session)
    ).stream() as streamer:
        async for event in streamer:
            if event_is_pull_request_opened(event):
                yield event


async def execute(
    seed_prs: List[int], dry: bool = False, database_url: str = None
) -> None:
    session = aiohttp.ClientSession()
    pr_stream = stream.map(
        aiter_opened_prs(seed_prs, session=session),
        partial(get_ofborg_eval, session=session),
        ordered=False,
    )

    if dry:
        sqs_queues = None
        autoscaling = None
    else:
        sqs_queues = get_sqs()
        autoscaling = get_autoscaling()
        assert database_url is not None

    if database_url is not None:
        conn = await asyncpg.connect(database_url)
        await conn.execute(create_nixpkgs_review_dispatched_table_sql())
    else:
        conn = None

    log.info("Setup", sqs=sqs_queues, autoscaling=autoscaling, conn=conn)
    async with pr_stream.stream() as streamer:
        async for event, ofborg_eval in streamer:
            pr = event["payload"]["number"]
            log.info("Main loop", pr=pr)

            if ofborg_eval is None:
                log.info(
                    "Ofborg failed or no packages",
                    pr=pr,
                    ofborg_eval=ofborg_eval,
                    failed=True,
                )
                # Ofborg failed
                continue

            log.info("New buildable PR", pr=pr, ofborg_eval=ofborg_eval)
            await log_buildable_pr(conn, pr=pr, ofborg_eval=ofborg_eval)
            if sqs_queues is not None:
                for system in ALL_BUILD_SYSTEMS:
                    if len(ofborg_eval["packages_per_system"].get(system, set())) == 0:
                        log.info("Empty pull request", pr=pr, system=system)
                        continue

                    sqs_response = sqs_queues[system].send_message(
                        # Message must be shorter than 2048 bytes, so don't pack
                        # too much stuff in here
                        MessageBody=json.dumps(
                            dict(
                                pr=pr,
                                ofborg_url=ofborg_eval["url"],
                            )
                        )
                    )
                    if sqs_response["ResponseMetadata"]["HTTPStatusCode"] != 200:
                        log.error("SQS Response", response=sqs_response, pr=pr)

            else:
                log.info(
                    "Skipping SQS submission",
                    pr=pr,
                    sqs_queues=sqs_queues,
                )


def main():
    configure_logging()
    p = argparse.ArgumentParser(
        description="""
Starts a process that listens to the nixpkgs github event stream and
posts PR numbers to SQS every time a new PR is opened with a completed ofborg
evaluation.

In order to seed me with extra PRs, you can also pass PRs on startup through the
command line or post messages to me via curl:

    $ curl -d '{"pr": 119054}' 127.0.0.1:8080

Note that I'm only listening on localhost, so you'll have to be posting from the same
server.

""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--seed-prs", nargs="+", type=int, default=[])
    p.add_argument(
        "--dry", action="store_true", help="Don't send events downstream to AWS"
    )
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = p.parse_args()

    return asyncio.run(
        execute(seed_prs=args.seed_prs, dry=args.dry, database_url=args.database_url)
    )
