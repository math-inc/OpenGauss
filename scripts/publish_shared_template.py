#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TAGS = ["template", "gauss"]


class PublishError(RuntimeError):
    """Raised when shared-template publishing fails."""


class NotFoundError(PublishError):
    """Raised when a Morph resource does not exist."""


@dataclass(frozen=True)
class TemplateMetadata:
    name: str
    description: str
    yaml_text: str


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def log(message: str) -> None:
    print(message, flush=True)


def read_template_metadata(path: Path) -> TemplateMetadata:
    yaml_text = path.read_text(encoding="utf-8")
    name = None
    description = None
    for line in yaml_text.splitlines():
        if not line or line.startswith("#") or line.startswith(" "):
            continue
        if line.startswith("name:"):
            name = strip_quotes(line.split(":", 1)[1])
        elif line.startswith("description:"):
            description = strip_quotes(line.split(":", 1)[1])
        if name and description:
            break
    if not name or not description:
        raise PublishError(f"Template file {path} must define top-level name and description fields.")
    return TemplateMetadata(name=name, description=description, yaml_text=yaml_text)


class MorphClient:
    def __init__(self, base_url: str, api_key: str, opener=urllib.request.urlopen):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._opener = opener

    def _request(self, method: str, path: str, payload: dict | None = None, timeout: int = 120) -> dict:
        url = path if path.startswith("http://") or path.startswith("https://") else f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            if exc.code == 404:
                raise NotFoundError(f"{method} {url} returned 404: {raw}") from exc
            raise PublishError(f"{method} {url} failed with HTTP {exc.code}: {raw}") from exc
        except urllib.error.URLError as exc:
            raise PublishError(f"{method} {url} failed: {exc}") from exc
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise PublishError(f"{method} {url} returned invalid JSON: {body!r}") from exc

    def fetch_alias(self, alias: str) -> dict | None:
        try:
            return self._request("GET", f"/api/aliases/{alias}")
        except NotFoundError:
            return None

    def create_template(self, *, metadata: TemplateMetadata, base_snapshot_id: str) -> dict:
        return self._request(
            "POST",
            "/api/templates",
            {
                "name": metadata.name,
                "description": metadata.description,
                "yaml": metadata.yaml_text,
                "baseSnapshotId": base_snapshot_id,
            },
        )

    def cache_template(self, template_id: str) -> dict:
        return self._request("POST", f"/api/templates/{template_id}/cache", {})

    def fetch_template(self, template_id: str) -> dict:
        return self._request("GET", f"/api/templates/{template_id}")

    def share_template(self, *, template_id: str, alias: str, description: str, tags: list[str]) -> dict:
        return self._request(
            "POST",
            f"/api/templates/{template_id}/share",
            {"alias": alias, "description": description, "tags": tags},
        )

    def delete_template(self, template_id: str) -> dict:
        return self._request("DELETE", f"/api/templates/{template_id}")


def parse_tags(raw_tags: str | None) -> list[str]:
    if not raw_tags:
        return []
    return [tag.strip() for tag in raw_tags.split(",") if tag.strip()]


def wait_for_ready(client: MorphClient, template_id: str, *, timeout_seconds: int, poll_seconds: float) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while True:
        template = client.fetch_template(template_id)
        current_status = template.get("status")
        if current_status != last_status:
            log(f"template_status {template_id} {current_status}")
            last_status = current_status
        if current_status == "ready":
            return template
        if current_status in {"failed", "cancelled", "error"}:
            raise PublishError(f"Template {template_id} entered terminal status {current_status}: {json.dumps(template)}")
        if time.monotonic() >= deadline:
            raise PublishError(f"Timed out waiting for template {template_id} to become ready; last status was {current_status!r}.")
        time.sleep(poll_seconds)


def wait_for_alias_target(
    client: MorphClient,
    alias: str,
    template_id: str,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while True:
        alias_state = client.fetch_alias(alias)
        if alias_state and alias_state.get("template_id") == template_id:
            return alias_state
        if time.monotonic() >= deadline:
            raise PublishError(f"Alias {alias!r} did not update to template {template_id!r} before timeout.")
        time.sleep(poll_seconds)


def wait_for_alias_missing(
    client: MorphClient,
    alias: str,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        alias_state = client.fetch_alias(alias)
        if alias_state is None:
            return
        if time.monotonic() >= deadline:
            raise PublishError(f"Alias {alias!r} still existed after deleting its shared template.")
        time.sleep(poll_seconds)


def publish_template(
    client: MorphClient,
    *,
    alias: str,
    template_path: Path,
    base_snapshot_id: str | None = None,
    tags: list[str] | None = None,
    timeout_seconds: int = 1800,
    poll_seconds: float = 5.0,
) -> dict:
    metadata = read_template_metadata(template_path)
    alias_state = client.fetch_alias(alias)
    resolved_base_snapshot = base_snapshot_id or (alias_state or {}).get("base_snapshot_id")
    if not resolved_base_snapshot:
        raise PublishError(
            f"Template alias {alias!r} does not exist and TEMPLATE_BASE_SNAPSHOT_ID was not provided."
        )
    resolved_tags = tags or (alias_state or {}).get("tags") or DEFAULT_TAGS
    log(f"publishing_alias {alias} base_snapshot={resolved_base_snapshot} tags={','.join(resolved_tags)}")

    created = client.create_template(metadata=metadata, base_snapshot_id=resolved_base_snapshot)
    template_id = created.get("id")
    if not template_id:
        raise PublishError(f"Create template response did not include an id: {json.dumps(created)}")
    log(f"created_template {template_id}")

    cache_result = client.cache_template(template_id)
    cache_run_id = cache_result.get("run_id") or cache_result.get("runId")
    if cache_run_id:
        log(f"cache_started {cache_run_id}")

    ready = wait_for_ready(client, template_id, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds)
    log(f"template_ready {template_id} status={ready.get('status')} final_snapshot={ready.get('final_snapshot_id')}")

    if alias_state:
        current_template_id = alias_state.get("template_id")
        if not current_template_id:
            raise PublishError(f"Alias {alias!r} did not include a template_id: {json.dumps(alias_state)}")
        client.delete_template(current_template_id)
        log(f"deleted_template {current_template_id}")
        wait_for_alias_missing(client, alias, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds)
        log(f"alias_freed {alias}")

    share_result = client.share_template(
        template_id=template_id,
        alias=alias,
        description=metadata.description,
        tags=resolved_tags,
    )
    log(f"alias_shared {alias}")

    alias_after = wait_for_alias_target(
        client,
        alias,
        template_id,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )

    return {
        "template_id": template_id,
        "alias": alias,
        "description": metadata.description,
        "tags": resolved_tags,
        "cache_run_id": cache_run_id,
        "share_result": share_result,
    }


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise PublishError(f"Environment variable {name} is required.")
    return value


def main() -> int:
    try:
        client = MorphClient(
            base_url=env("DEVBOX_TEMPLATE_BASE_URL"),
            api_key=env("MORPH_API_KEY"),
        )
        result = publish_template(
            client,
            alias=env("TEMPLATE_ALIAS"),
            template_path=Path(env("TEMPLATE_FILE")),
            base_snapshot_id=os.environ.get("TEMPLATE_BASE_SNAPSHOT_ID", "").strip() or None,
            tags=parse_tags(os.environ.get("TEMPLATE_TAGS")),
            timeout_seconds=int(os.environ.get("TEMPLATE_TIMEOUT_SECONDS", "1800")),
            poll_seconds=float(os.environ.get("TEMPLATE_POLL_SECONDS", "5")),
        )
    except PublishError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
