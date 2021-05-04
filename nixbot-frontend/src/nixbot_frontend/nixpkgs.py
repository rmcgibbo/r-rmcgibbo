import asyncio
import os
import time
import urllib.parse
from collections import defaultdict
from typing import Any, AsyncIterator, DefaultDict, Dict, Optional, Set, Tuple

import aiohttp
from loguru import logger as log
from nixbot_common import removeprefix
from typing_extensions import TypedDict

Event = Dict
NIXPKGS_EVENTS = "https://api.github.com/repos/nixos/nixpkgs/events"
BLACKLISTED_ATTRS = {
    "tests.nixos-functions.nixos-test",
    "tests.nixos-functions.nixosTest-test",
}
OfborgEval = TypedDict(
    "OfborgEval",
    {
        "url": str,
        "packages_per_system": Dict[str, Set[str]],
    },
)


def github_headers() -> Dict[str, str]:
    return {
        "Authorization": "token %s" % os.environ["GITHUB_TOKEN"],
        "Accept": "application/vnd.github.v3+json",
    }


def union(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    c = dict(a)
    c.update(**b)
    return c


async def aiter_nixpkgs_events(session: aiohttp.ClientSession) -> AsyncIterator[Event]:
    base_headers = github_headers()
    req_headers: Dict[str, Any] = {}
    prv_events: Optional[Dict[str, Any]] = None
    def_poll_interval = 60.0

    while True:
        async with session.get(
            NIXPKGS_EVENTS, headers=union(base_headers, req_headers)
        ) as resp:
            resp_log_data = dict(
                rate_limits={k: v for k, v in resp.headers.items() if "RateLimit" in k},
                code=resp.status,
            )
            if "Etag" not in resp.headers:
                resp_log_data["response_body"] = await resp.read()
                resp_log_data["headers"] = resp.headers

            if resp.status == 401:
                log.error(resp.headers)
                log.error(await resp.text())
                raise RuntimeError()

            if resp.status == 200:
                json_body = await resp.json()
                cur_events = {e["id"]: e for e in json_body}

                resp_log_data["n_events_received"] = len(json_body)

                if prv_events is not None:
                    new_events = {
                        k: v for k, v in cur_events.items() if k not in prv_events
                    }

                    for e in new_events.values():
                        yield e

                prv_events = cur_events

        poll_interval = float(resp.headers.get("X-Poll-Interval", def_poll_interval))
        log.info("Waiting for new events", sleep_time=poll_interval, **resp_log_data)
        await asyncio.sleep(poll_interval)

        req_headers = (
            {"If-None-Match": removeprefix(resp.headers["Etag"], "W/")}
            if "Etag" in resp.headers
            else {}
        )


async def get_ofborg_gist_data(
    target_url: str, session: aiohttp.ClientSession
) -> Dict[str, Set[str]]:
    url = urllib.parse.urlparse(target_url)
    assert len(url.path) != 0
    raw_gist_url = f"https://gist.githubusercontent.com/GrahamcOfBorg{url.path}/raw/"

    packages_per_system: DefaultDict[str, Set[str]] = defaultdict(set)
    async with session.get(raw_gist_url) as resp:
        for line in (await resp.text("utf-8")).splitlines():
            system, attribute = line.split()
            if attribute not in BLACKLISTED_ATTRS:
                packages_per_system[system].add(attribute)
    return dict(packages_per_system)


async def get_ofborg_eval(
    event: Dict, session: aiohttp.ClientSession
) -> Tuple[Event, Optional[OfborgEval]]:
    clog = log.bind(pr=event["payload"]["number"])
    pull_request_url = event["payload"]["pull_request"]["_links"]["self"]["href"]
    default_poll_interval = 60.0  # 60 seconds
    timeout_deadline = time.time() + 6 * 3600  # 6 hours
    FAILURE_MSG = "This PR does not cleanly list package outputs after merging."

    while True:
        async with session.get(pull_request_url, headers=github_headers()) as pr_resp:
            pr_data = await pr_resp.json()

        if time.time() > timeout_deadline:
            clog.error("Ofborg timeout, or infinite loop")
            return event, None

        if pr_data.get("draft"):
            clog.info("Sleeping on draft PR")
            await asyncio.sleep(default_poll_interval)
            continue

        if "statuses_url" not in pr_data:
            clog.error("Malformed pull request respose from github", body=pr_data)
            await asyncio.sleep(default_poll_interval)
            continue

        async with session.get(
            pr_data["statuses_url"], headers=github_headers()
        ) as resp:
            rjson = await resp.json()

        for status in rjson:
            if not isinstance(status, dict):
                clog.error("Ofborg error. status not dict?", status=status)
                continue

            if status.get("description") == "^.^!" and status.get("state") == "success":
                if status["target_url"] == "":
                    clog.info("Ofborg reports no packages", state="finished")
                    return event, None

                packages_per_system = await get_ofborg_gist_data(
                    status["target_url"], session
                )
                clog.info("Ofborg success", state="finished")
                return event, {
                    "url": status["target_url"],
                    "packages_per_system": packages_per_system,
                }

            if (
                status.get("description") == FAILURE_MSG
                and status.get("state") == "failure"
            ):
                clog.info("Ofborg failure", state="finished")
                return event, None

        clog.info(
            "Waiting for ofborg",
            sleep_time=default_poll_interval,
            code=resp.status,
        )
        await asyncio.sleep(default_poll_interval)


def event_is_pull_request_opened(e: Event) -> bool:
    if e["type"] == "PullRequestEvent" and e["payload"]["action"] == "opened":
        base_label = (
            e["payload"].get("pull_request", {}).get("base", {}).get("label", "ERROR")
        )
        is_skipped = base_label in ("NixOS:haskell-updates",)

        log.info(
            "New PR opened",
            pr=e["payload"]["number"],
            base=base_label,
            is_skipped=is_skipped,
        )
        if is_skipped:
            return False

        return True
    return False


async def pr_number_as_pull_event(pr: int, session: aiohttp.ClientSession) -> Event:
    pull_url = f"https://api.github.com/repos/NixOS/nixpkgs/pulls/{pr}"
    async with session.get(pull_url, headers=github_headers()) as resp:
        if resp.status != 200:
            # Could be auth problem with github token like
            # {'message': 'Bad credentials', 'documentation_url': 'https://docs.github.com/rest'}}}
            # Or something else?
            log.error(
                headers=resp.headers, status=resp.status, body=(await resp.json())
            )
            raise RuntimeError(await resp.json())

        body = await resp.json()
        return {
            "type": "PullRequestEvent",
            "payload": {"action": "opened", "number": pr, "pull_request": body},
        }
