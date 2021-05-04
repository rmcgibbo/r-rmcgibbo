from __future__ import annotations

import argparse
import itertools
import re
import sqlite3
import statistics

import pyfst
from humanize import naturalsize


def deversion_nix_drv_name(nix_name: str) -> str:
    parts = nix_name.split("-")
    new_parts = []
    for part in parts:
        m = re.match(r"[0-9\.]+$", part)
        if m is not None:
            part = re.sub(r"\d+", "#", part)
        new_parts.append(part)
    return "-".join(new_parts)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("sqlite_db", default="build-times.db")
    p.add_argument("fst", default="build-times.fst")
    p.add_argument("-w", "--where", default="WHERE true")
    args = p.parse_args()

    db = sqlite3.connect(args.sqlite_db)
    cur = db.execute(
        f"""
SELECT nix_name, cast(duration as int)
FROM build
{args.where}
ORDER BY nix_name
"""
    )

    #
    # Record both the name of the package and the sanitized name of the package
    # where 'sanitization' removes numbers, so that 'adoptopenjdk-hotspot-bin-15.0.1'
    # becomes 'adoptopenjdk-hotspot-bin-#.#.#'
    #
    sanitized = []
    for nix_name, value in cur:
        sname = deversion_nix_drv_name(nix_name)

        sanitized.append((nix_name, value))
        sanitized.append((sname, value))

    medians = []
    for nix_name, group in itertools.groupby(sorted(sanitized), lambda x: x[0]):
        median = int(statistics.median([g[1] for g in group]))
        medians.append((nix_name, median))

    # pyfst.write raises an error if they're not unique + sorted
    assert len(medians) == len({n for n, _ in medians})
    nbytes = pyfst.write(args.fst, medians)

    print(f"FST data written: {naturalsize(nbytes)}")
    return 0
