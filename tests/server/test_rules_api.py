import json

import pytest
import yaml

from openviking.service.rule_service import RULE_SCHEMA, SCENARIO_SCHEMA
from openviking_cli.utils.config.vlm_config import VLMConfig
from tests.server.test_api_resources import _wait_task_terminal


@pytest.mark.asyncio
async def test_rule_routes_reject_empty_inputs(client):
    compile_response = await client.post("/api/v1/rules/compile", json={})
    assert compile_response.status_code == 400, compile_response.text
    compile_error = compile_response.json()["error"]
    assert compile_error["code"] == "INVALID_ARGUMENT"
    assert "instruction" in compile_error["message"]
    assert "source" in compile_error["message"]

    search_response = await client.post("/api/v1/rules/search", json={"task_context": " "})
    assert search_response.status_code == 400, search_response.text
    search_error = search_response.json()["error"]
    assert search_error["code"] == "INVALID_ARGUMENT"
    assert "task_context" in search_error["message"]


@pytest.mark.asyncio
async def test_rule_compile_route_runs_with_real_task_tracker(client, app, monkeypatch):
    app.state.config.temp_upload.default_mode = "shared"
    content = "# Review guidelines\n\nConfirm before deleting files."
    uploaded = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("guidelines.md", content, "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text
    upload_id = uploaded.json()["result"]["temp_file_id"]
    assert upload_id.startswith("shared_")

    completion = VLMConfig.get_completion_async
    rule_text = "Confirm before deleting files."
    plan_reason = "Reviewed the uploaded guidelines."

    async def complete_rule_plan(vlm, prompt, thinking=False, **kwargs):
        if "You are a semantic compiler." not in prompt:
            return await completion(vlm, prompt, thinking=thinking, **kwargs)
        _, _, encoded_sources = prompt.partition("\nparsed_sources:\n")
        parsed_sources, _ = json.JSONDecoder().raw_decode(encoded_sources)
        assert content in parsed_sources
        return json.dumps(
            {
                "reason": plan_reason,
                "mutations": [
                    {
                        "operation": "NEW",
                        "evidence_spans": [rule_text],
                        "record": {
                            "name": "confirm-delete",
                            "description": rule_text,
                            "content": rule_text,
                            "body": f"# Confirm deletion\n\n{rule_text}",
                        },
                        "scenario": {
                            "scenario_id": "file-deletion",
                            "name": "File deletion",
                            "description": "Operations that delete files.",
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr(VLMConfig, "get_completion_async", complete_rule_plan)

    response = await client.post(
        "/api/v1/rules/compile",
        json={"sources": [{"temp_file_id": upload_id, "wait": False, "internal_task": False}]},
    )
    assert response.status_code == 202, response.text
    task_id = response.json()["result"]["task_id"]
    task = await _wait_task_terminal(client, task_id)
    assert task["status"] == "completed", task
    assert task["stage"] == "completed"
    assert task["result"]["plan_reason"] == plan_reason
    [root] = task["result"]["source_uris"]
    read = await client.get("/api/v1/content/read", params={"uri": f"{root}/guidelines.md"})
    assert read.status_code == 200, read.text
    assert read.json()["result"].strip() == content

    tasks = {"task_type": "add_resource", "resource_id": root}
    visible = await client.get("/api/v1/tasks", params=tasks)
    assert visible.json()["result"] == []
    internal = await client.get("/api/v1/tasks", params={**tasks, "include_internal": True})
    assert [item["status"] for item in internal.json()["result"]] == ["completed"]

    details = await client.get(f"/api/v1/tasks/{task_id}", params={"include_events": True})
    assert details.status_code == 200, details.text
    events = details.json()["result"]["execution_events"]["items"]
    assert [event["stage"] for event in events if event["kind"] == "stage_changed"] == [
        "queued",
        "ingesting_sources",
        "recalling_rules",
        "planning_operations",
        "writing_rules",
    ]

    result = task["result"]
    [operation] = result["operations"]
    assert operation["operation"] == "NEW"
    assert operation["atomic_id"] == "confirm-delete"
    rule_uri = operation["uri"]
    write_task_id = result["write_result"]["task_id"]
    assert write_task_id != task_id
    write_task = await _wait_task_terminal(client, write_task_id)
    assert write_task["status"] == "completed", write_task
    assert (await _wait_task_terminal(client, task_id))["result"] == result
    persisted = await client.get("/api/v1/content/read", params={"uri": rule_uri})
    assert persisted.status_code == 200, persisted.text
    contents = persisted.json()["result"]
    metadata = yaml.safe_load(contents.split("---", 2)[1])
    assert metadata["schema"] == RULE_SCHEMA
    assert metadata["status"] == "active"
    assert metadata["source_uris"] == [root]
    assert metadata["confidence"] == "user"
    assert metadata["content"] == rule_text
    assert contents.endswith(f"# Confirm deletion\n\n{rule_text}")
    scenario_uri = rule_uri.rsplit("/", 1)[0] + "/abstract.md"
    assert result["written_uris"] == [scenario_uri, rule_uri]
    scenario = await client.get("/api/v1/content/read", params={"uri": scenario_uri})
    assert scenario.status_code == 200, scenario.text
    scenario_metadata = yaml.safe_load(scenario.json()["result"].split("---", 2)[1])
    assert scenario_metadata["schema"] == SCENARIO_SCHEMA
    assert scenario_metadata["scenario_id"] == "file-deletion"
    assert "rule_uris" not in scenario_metadata

    search = await client.post(
        "/api/v1/rules/search", json={"task_context": "Delete a generated file."}
    )
    assert search.status_code == 200, search.text
    result = search.json()["result"]
    [matched] = result["rules"]
    assert matched["uri"] == rule_uri
    assert matched["atomic_id"] == "confirm-delete"
    assert rule_text in matched["content"]
    assert result["delivery"]["rule_count"] == 1
    assert rule_text in result["rendered"]
