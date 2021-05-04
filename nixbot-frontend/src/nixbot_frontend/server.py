import asyncio
import json
from functools import partial

import aiohttp
from aiohttp import web

from .nixpkgs import pr_number_as_pull_event


async def handle_request(queue: asyncio.Queue, request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except json.decoder.JSONDecodeError:
        raise web.HTTPBadRequest()
    try:
        pr: int = data["pr"]
        assert isinstance(pr, int)
    except (KeyError, AssertionError):
        raise web.HTTPUnprocessableEntity()

    queue.put_nowait(pr)
    return web.json_response({"status": "ok"})


async def server(queue: asyncio.Queue) -> None:
    app = web.Application()
    app.add_routes(
        [
            web.post("/", partial(handle_request, queue)),
        ]
    )

    # https://docs.aiohttp.org/en/stable/web_advanced.html#aiohttp-web-app-runners
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 8080)
    await site.start()
    while True:
        await asyncio.sleep(3600)  # sleep forever


async def aiter_server_events(client_session: aiohttp.ClientSession):
    queue: asyncio.Queue = asyncio.Queue()
    server_task: asyncio.Task = asyncio.create_task(server(queue))

    while True:
        pr: int = await queue.get()
        event = await pr_number_as_pull_event(pr, client_session)
        yield event

    # Wait for server to shutdown (never)
    await asyncio.gather(server_task, return_exceptions=True)
