from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "publish_shared_template.py"


def load_module():
    spec = importlib.util.spec_from_file_location("publish_shared_template", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeOpener:
    def __init__(self, handlers):
        self.handlers = handlers
        self.requests = []

    def __call__(self, request, timeout=120):
        self.requests.append((request.get_method(), request.full_url, request.data, timeout))
        if not self.handlers:
            raise AssertionError(f"Unexpected request {request.get_method()} {request.full_url}")
        return self.handlers.pop(0)(request)


def test_read_template_metadata_requires_top_level_name_and_description(tmp_path):
    module = load_module()
    template = tmp_path / "template.yaml"
    template.write_text(
        "name: Test Template\n"
        "description: Ready session without extra setup questions.\n"
        "steps:\n"
        "  - id: one\n"
        "    type: command\n"
        "    run: echo hi\n",
        encoding="utf-8",
    )
    metadata = module.read_template_metadata(template)
    assert metadata.name == "Test Template"
    assert metadata.description == "Ready session without extra setup questions."


def test_publish_template_reuses_alias_snapshot_and_tags(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    template = tmp_path / "template.yaml"
    template.write_text(
        "name: Test Template\n"
        "description: Ready session without extra setup questions.\n"
        "steps:\n"
        "  - id: one\n"
        "    type: command\n"
        "    run: echo hi\n",
        encoding="utf-8",
    )

    def alias_before(_request):
        return FakeResponse(
            {
                "alias": "demo",
                "base_snapshot_id": "snap_base",
                "tags": ["template", "gauss"],
                "template_id": "tpl_current",
            }
        )

    def create_template(request):
        body = json.loads(request.data.decode("utf-8"))
        assert body["baseSnapshotId"] == "snap_base"
        assert body["name"] == "Test Template"
        assert "extra setup questions" in body["description"]
        assert "steps:" in body["yaml"]
        return FakeResponse({"id": "tpl_new", "status": "draft"})

    def cache_template(_request):
        return FakeResponse({"template_id": "tpl_new", "run_id": "run_1"})

    template_polls = iter(
        [
            {"id": "tpl_new", "status": "building"},
            {"id": "tpl_new", "status": "ready", "final_snapshot_id": "snap_new"},
        ]
    )

    def fetch_template(_request):
        return FakeResponse(next(template_polls))

    def delete_template(_request):
        return FakeResponse({})

    def alias_missing(_request):
        raise HTTPError("https://devbox.example.test/api/aliases/demo", 404, "not found", hdrs=None, fp=None)

    def share_template(request):
        body = json.loads(request.data.decode("utf-8"))
        assert body["alias"] == "demo"
        assert body["tags"] == ["template", "gauss"]
        return FakeResponse({"published": True})

    def alias_after(_request):
        return FakeResponse({"alias": "demo", "template_id": "tpl_new", "tags": ["template", "gauss"]})

    opener = FakeOpener(
        [alias_before, create_template, cache_template, fetch_template, fetch_template, delete_template, alias_missing, share_template, alias_after]
    )
    client = module.MorphClient("https://devbox.example.test", "token", opener=opener)

    result = module.publish_template(
        client,
        alias="demo",
        template_path=template,
        timeout_seconds=1,
        poll_seconds=0,
    )

    assert result["template_id"] == "tpl_new"
    assert result["alias"] == "demo"
    assert result["tags"] == ["template", "gauss"]
    assert [method for method, *_ in opener.requests] == ["GET", "POST", "POST", "GET", "GET", "DELETE", "GET", "POST", "GET"]


def test_publish_template_requires_base_snapshot_for_new_alias(tmp_path):
    module = load_module()

    template = tmp_path / "template.yaml"
    template.write_text(
        "name: Test Template\n"
        "description: Ready session without extra setup questions.\n",
        encoding="utf-8",
    )

    def alias_missing(request):
        raise HTTPError(request.full_url, 404, "not found", hdrs=None, fp=None)

    client = module.MorphClient("https://devbox.example.test", "token", opener=FakeOpener([alias_missing]))

    with pytest.raises(module.PublishError, match="TEMPLATE_BASE_SNAPSHOT_ID"):
        module.publish_template(client, alias="missing", template_path=template, timeout_seconds=1, poll_seconds=0)


def test_publish_template_creates_and_shares_when_alias_is_missing(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    template = tmp_path / "template.yaml"
    template.write_text(
        "name: Test Template\n"
        "description: Ready session without extra setup questions.\n"
        "steps:\n"
        "  - id: one\n"
        "    type: command\n"
        "    run: echo hi\n",
        encoding="utf-8",
    )

    def alias_missing(request):
        raise HTTPError(request.full_url, 404, "not found", hdrs=None, fp=None)

    def create_template(request):
        body = json.loads(request.data.decode("utf-8"))
        assert body["baseSnapshotId"] == "snap_base"
        return FakeResponse({"id": "tpl_new", "status": "draft"})

    def cache_template(_request):
        return FakeResponse({"run_id": "run_1"})

    template_polls = iter(
        [
            {"id": "tpl_new", "status": "building"},
            {"id": "tpl_new", "status": "ready", "final_snapshot_id": "snap_new"},
        ]
    )

    def fetch_template(_request):
        return FakeResponse(next(template_polls))

    def share_template(request):
        body = json.loads(request.data.decode("utf-8"))
        assert body["alias"] == "demo"
        assert body["tags"] == ["template", "gauss"]
        return FakeResponse({"published": True})

    def alias_after(_request):
        return FakeResponse({"alias": "demo", "template_id": "tpl_new", "tags": ["template", "gauss"]})

    opener = FakeOpener([alias_missing, create_template, cache_template, fetch_template, fetch_template, share_template, alias_after])
    client = module.MorphClient("https://devbox.example.test", "token", opener=opener)

    result = module.publish_template(
        client,
        alias="demo",
        template_path=template,
        base_snapshot_id="snap_base",
        timeout_seconds=1,
        poll_seconds=0,
    )

    assert result["template_id"] == "tpl_new"
    assert result["alias"] == "demo"
