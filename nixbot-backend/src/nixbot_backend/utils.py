from contextlib import contextmanager
from typing import Any, Dict, Iterable

from systemd import journal as journald


def journald_logs_since(
    match: Dict[str, str], start_time: float
) -> Iterable[Dict[str, Any]]:
    journal = journald.Reader()
    journal.add_match(**match)
    journal.seek_tail()

    journal.seek_realtime(start_time)
    journal.get_previous()
    return journal


@contextmanager
def with_distributed_lock(enabled: bool, lock_key: str):
    # https://python-dynamodb-lock.readthedocs.io/en/latest/usage.html
    if not enabled:
        return

    import boto3
    from python_dynamodb_lock.python_dynamodb_lock import DynamoDBLockClient

    with DynamoDBLockClient(boto3.resource("dynamodb")).acquire_lock(lock_key):
        yield
