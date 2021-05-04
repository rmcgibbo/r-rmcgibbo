from __future__ import annotations

import functools
import io
import os
import statistics
import subprocess
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, TypeVar

import networkx as nx
import pyfst
from loguru import logger as log
from nixbot_common import removeprefix
from statx import stat, stat_result
from typing_extensions import TypedDict

from .compile_fst import deversion_nix_drv_name

T = TypeVar("T")


ReportJson = TypedDict("ReportJson", {
    "blacklisted": List[str],
    "broken": List[str],
    "built": List[str],
    "failed": List[str],
    "non-existant": List[str],
    "pr": int,
    "pr_rev": str,
    "skipped": List[str],
    "system": str,
    "tests": List[str],
    "timed_out": List[str],

    # Stuff we add at the end of post_build_hook
    "hammer_report": Optional[str],
    "uploaded": bool,
    "blocked_reason": str,
    "num_suggestions": int,
}, total=False)


@dataclass
class Attr:
    name: str
    exists: bool
    broken: bool
    blacklisted: bool
    skipped: bool
    path: Optional[str]
    drv_path: Optional[str]
    position: Optional[str]
    log_url: Optional[str] = field(default=None)
    aliases: List[str] = field(default_factory=lambda: [])
    timed_out: bool = field(default=False)
    build_err_msg: Optional[str] = field(default=None)
    _path_verified: Optional[bool] = field(default=None)

    def __hash__(self):
        return hash((self.name, self.drv_path, self.path))

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    def filename(self) -> Optional[str]:
        if self.position is not None:
            return os.path.relpath(self.position.split(":")[0], _get_nixpkgs())
        return None


def get_build_time(drv_path: str) -> Optional[timedelta]:
    def get_log_path(drv_path: str) -> Optional[str]:
        if drv_path is None:
            return None
        base = os.path.basename(drv_path)
        prefix = "/nix/var/log/nix/drvs/"
        full = os.path.join(prefix, base[:2], base[2:] + ".bz2")
        if os.path.exists(full):
            return full
        return None

    log_path = get_log_path(drv_path)
    if log_path is None:
        return None
    result = stat(log_path)
    assert isinstance(result, stat_result)
    return timedelta(microseconds=(result.st_mtime_ns - result.st_birthtime_ns) / 1000)


def get_build_graph(
    drvpath_targets: Iterable[str], drvpath_universe: List[str] = None
) -> nx.DiGraph:
    """Build graph a a DiGraph.
    Each edge (i, j) means that drv `i` is required as input to drv `j`.
    """
    start_time = time.time()
    targets = list(drvpath_targets)

    if len(targets) == 0:
        return nx.DiGraph()

    def graph_ml_chunks():
        # Work around `OSError: [Errno 7] Argument list too long: 'nix-store'`
        # by chunking.
        for chunk in _chunker(targets, 5000):
            graph_ml = subprocess.run(
                ["nix-store", "--query", "--graphml"] + chunk, stdout=subprocess.PIPE
            )
            yield nx.read_graphml(io.BytesIO(graph_ml.stdout))

    g = nx.compose_all(graph_ml_chunks())

    # At this point, the nodes to not have /nix/store in their names
    # And it includes _everything_, down to glibc

    PFX = "/nix/store/"

    if drvpath_universe is not None:
        to_remove = set(g.nodes) - {
            removeprefix(drv_path, PFX) for drv_path in drvpath_universe
        }
        to_remove -= {removeprefix(t, PFX) for t in targets}
        g.remove_nodes_from(to_remove)

    nx.relabel_nodes(g, {n: f"{PFX}{n}" for n in g.nodes}, copy=False)

    if time.time() - start_time > 2:
        log.info(f"Computing build graph: {time.time() - start_time:.2f} sec")
    return g


@functools.lru_cache()
def current_system() -> str:
    system = subprocess.run(
        [
            "nix",
            "--experimental-features",
            "nix-command",
            "eval",
            "--impure",
            "--raw",
            "--expr",
            "builtins.currentSystem",
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return system.stdout


def build_dry(
    drvs: List[str],
) -> Tuple[Set[str], Set[str]]:
    """Returns a list of drvs to be built and fetched in order to
    realize `drvs`"""
    N_MAX_ATTRS = 2 ** 12
    if len(drvs) > N_MAX_ATTRS:
        raise NotImplementedError("This requires chunking")

    start = time.time()
    cmd = ["nix-store", "--realize", "--dry-run"] + drvs
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    end = time.time()

    lines = result.stderr.splitlines()
    to_fetch: List[str] = []
    to_build: List[str] = []
    ignore: List[str] = []
    for line in lines:
        line = line.strip()
        if "will be fetched" in line:
            cur = to_fetch
        elif "will be built" in line:
            cur = to_build
        elif "don't know how to build" in line:
            cur = ignore
        elif "querying info about" in line:
            cur = ignore
        elif "downloading" in line and ".narinfo" in line:
            cur = ignore
        elif "warning: unable to download" in line:
            cur = ignore
        elif line.startswith("/nix/store"):
            cur.append(line)
        elif line != "":
            raise RuntimeError(f"dry-run parsing failed: '{line}'. lines={result.stderr}")

    if end - start > 2:
        log.info(f"Computing nix build --dry-run: {end-start:.2f} sec")

    return set(to_build), set(to_fetch)


def get_estimated_build_times(drv_paths: List[str]) -> Dict[str, float]:
    NUM_MISSING_BUILD_PRINTS_CUTOFF = 50

    start_time = time.time()
    db = pyfst.load(os.path.join(os.path.dirname(__file__), "build-times.fst"))

    def get_drv_name(drv_path: str) -> str:
        return drv_path.split("-", 1)[1][:-4]

    def impl():
        num_missing_build_prints = 0

        for drv_path in sorted(drv_paths, key=get_drv_name):
            name = get_drv_name(drv_path)
            sname = deversion_nix_drv_name(name)

            if any(
                name.endswith(suffix)
                for suffix in [
                    "-config",
                    "-env",
                    "-fhs",
                    "-hook",
                    "-etc",
                    "-init",
                    "-info",
                    "-lib",
                    "-list",
                    "-lockfile",
                    "-merged",
                    "-multi",
                    "-paths",
                    "-params",
                    "-patched",
                    "-runtime",
                    "-sources",
                    "-target",
                    "-wrapped",
                    "-wrapper-",
                    ".7z",
                    ".cfg",
                    ".cmake",
                    ".conf",
                    ".d",
                    ".deb",
                    ".desktop",
                    ".diff",
                    ".fish",
                    ".gem",
                    ".ini",
                    ".h",
                    ".jar",
                    ".js",
                    ".json",
                    ".nix",
                    ".p",
                    ".patch",
                    ".pl",
                    ".png",
                    ".properties",
                    ".rpm",
                    ".rules",
                    ".run",
                    ".sed",
                    ".sh",
                    ".tar.bz2",
                    ".tar.gz",
                    ".tar.xz",
                    ".tgz",
                    ".toml",
                    ".whl",
                    ".zip",
                    "bash",
                    "bazel-deps",
                    "bazel-rc",
                    "chrootenv",
                    "ldconfig",
                    "ghostscript-fonts",
                    "go-bootstrap",
                    "offline",
                    "profile",
                    "remote_java_tools_linux",
                    "source",
                    "steam",
                    "x11env",
                ]
            ):
                yield (drv_path, 0)
                continue

            found = False
            for q, distance in (
                (name, 0),
                (name, 1),
                (sname, 0),
                (sname, 1),
            ):
                try:
                    rs = db.fuzzy(q, distance)
                except OSError:
                    rs = []
                if len(rs) > 0:
                    yield (drv_path, statistics.median([r[1] for r in rs]))
                    found = True
                    break

            if found:
                continue

            name_parts = name.split("-")
            if len(name_parts) > 0:
                for i in range(len(name_parts) - 1, 0, -1):
                    rs = db.prefix("-".join(name_parts[:i]))
                    if len(rs) > 0:
                        value = statistics.median([r[1] for r in rs])
                        if num_missing_build_prints < NUM_MISSING_BUILD_PRINTS_CUTOFF:
                            log.info(
                                f"Build-time estimate for {name} fell back to {'-'.join(name_parts[:i])}",
                                value=value,
                            )
                            num_missing_build_prints += 1

                        yield (drv_path, value)
                        found = True
                        break

            if found:
                continue

            if num_missing_build_prints < NUM_MISSING_BUILD_PRINTS_CUTOFF:
                log.info(f"Missing build-time information for {name}")
                num_missing_build_prints += 1
            elif num_missing_build_prints == NUM_MISSING_BUILD_PRINTS_CUTOFF:
                log.info("Further missing build-time information logs suppressed")
                num_missing_build_prints += 1
            yield (drv_path, None)

    data = dict(impl())
    n_missing = sum(1 for x in data.values() if x is None)
    end_time = time.time()
    log.info(f"Missing build time information for {n_missing}/{len(data)}")
    if (start_time - end_time) > 2:
        log.info(f"Estimating build time: {end_time - start_time:.2f} sec")
    return {k: v or 0 for k, v in data.items()}


def _chunker(seq: Iterable[T], size: int) -> Iterable[List[T]]:
    res = []
    for el in seq:
        res.append(el)
        if len(res) == size:
            yield res
            res = []
    if res:
        yield res


@functools.lru_cache()
def _get_nixpkgs() -> str:
    for section in os.environ["NIX_PATH"].split(":"):
        key, value = section.split("=")
        if key == "nixpkgs":
            return value
    raise RuntimeError(f"Incorrect NIX_PATH: {os.environ['NIX_PATH']}")
