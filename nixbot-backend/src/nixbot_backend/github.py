from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from unidiff import PatchSet


def pr_url(pr: int) -> str:
    return f"https://github.com/NixOS/nixpkgs/pull/{pr}"


class GithubClient:
    def __init__(self, api_token: Optional[str]) -> None:
        self.api_token = api_token

    def _request(
        self, path: str, method: str, data: Optional[Dict[str, Any]] = None
    ) -> Any:
        url = urllib.parse.urljoin("https://api.github.com/", path)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
        }
        if self.api_token:
            headers["Authorization"] = f"token {self.api_token}"

        body = None
        if data:
            body = json.dumps(data).encode("ascii")

        req = urllib.request.Request(url, headers=headers, method=method, data=body)
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())

    def get(self, path: str) -> Any:
        return self._request(path, "GET")

    def post(self, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        return self._request(path, "POST", data=data)

    def pull_request(self, number: int) -> Any:
        "Get a pull request"
        return self.get(f"repos/NixOS/nixpkgs/pulls/{number}")

    def pull_request_comments(self, number: int) -> Any:
        "Get comments on pull request"
        return self.get(f"repos/NixOS/nixpkgs/issues/{number}/comments")

    def graphql(self, query: str, variables: Dict[str, Any]) -> Any:
        return self.post("graphql", data={"query": query, "variables": variables})

    def load_patchset(self, number: int) -> PatchSet:
        "Get a pull request patchset"
        diff = urllib.request.urlopen(
            f"https://github.com/NixOS/nixpkgs/pull/{number}.diff"
        )
        encoding = diff.headers.get_charsets()[0]
        patch = PatchSet(diff, encoding=encoding)
        return patch


def determine_modified_files(patchset: PatchSet) -> List[str]:
    filenames = set()
    for f in patchset:
        if f.target_file.startswith("b/"):
            filenames.add(f.target_file[len("b/") :])
        if f.source_file.startswith("a/"):
            filenames.add(f.source_file[len("a/") :])
    return sorted(filenames)
