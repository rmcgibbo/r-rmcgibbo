"""nixpkgs_review_bot

"""
__version__ = "0"

import json
import os
import sys
import urllib.request
from typing import Any, Dict, Optional

from loguru import logger


def removeprefix(s: str, prefix: str, /) -> str:
    if s.startswith(prefix):
        return s[len(prefix) :]
    else:
        return s[:]


def format(istty: bool):
    if istty:
        base = [
            "<green>{time:YYYY-MM-DD HH:mm:ss zz}</green>",
            "<level>{message}</level>"
        ]
    else:
        base = ["<level>{message}</level>"]

    pr = os.environ.get("NIXPKGS_REVIEW_PR")

    def fmt(record: Dict[str, Any]) -> str:
        fields = []
        if "pr" in record["extra"]:
            fields.append("<blue>{extra[pr]}</blue>")
        elif pr is not None:
            fields.append(f"<blue>{pr}</blue>")

        fields.extend(base)
        remaining = sorted(k for k in record["extra"] if k != "pr")
        if len(remaining) > 0:
            fields.append(" ".join("%s={extra[%s]}" % (k, k) for k in remaining))

        return " | ".join(fields) + "\n{exception}"

    return fmt


def configure_logging(stderr_only=False) -> None:
    # By default, send debug/info to stdout and warning/error to stderr

    def less_than_warning(record):
        return record["level"].no < 30

    def geq_than_warning(record):
        return record["level"].no >= 30

    sink = sys.stderr if stderr_only else sys.stdout
    logger.configure(
        handlers=[
            {"sink": sink, "filter": less_than_warning, "format": format(sink.isatty())},
            {"sink": sys.stderr, "filter": geq_than_warning, "format": format(sys.stderr.isatty())},
        ]
    )


def isint(x: str) -> bool:
    try:
        _y = int(x)
        _y = _y
        return True
    except ValueError:
        return False


def get_ec2_metadata() -> Optional[Dict[str, Any]]:
    if os.path.exists("/etc/ec2-metadata/hostname"):
        req = urllib.request.Request(
            "http://169.254.169.254/latest/dynamic/instance-identity/document"
        )
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    return None


def create_nixpkgs_review_dispatched_table_sql() -> str:
    with open(os.path.join(os.path.dirname(__file__), "nixpkgs_review_dispatched.sql")) as f:
        return f.read()


def create_nixpkgs_review_finished_table_sql() -> str:
    with open(os.path.join(os.path.dirname(__file__), "nixpkgs_review_finished.sql")) as f:
        return f.read()
