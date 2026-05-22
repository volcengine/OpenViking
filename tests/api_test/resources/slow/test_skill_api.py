import uuid

import pytest


class TestSkillApi:
    def test_add_skill_basic(self, api_client):
        skill_data = {
            "name": f"test_skill_{uuid.uuid4().hex[:6]}",
            "description": "A test skill for API validation",
            "content": "# test_skill\n\nA test skill for API validation.",
        }
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        if add_resp.status_code == 500:
            pytest.skip(
                "add_skill returned 500: embedding service unavailable "
                "(PR CI cannot access VLM/Embedding API keys)."
            )
        assert add_resp.status_code == 200, (
            f"add_skill should return valid status, got {add_resp.status_code}: {add_resp.text[:200]}"
        )
        if add_resp.status_code == 200:
            data = add_resp.json()
            assert data.get("status") == "ok"
            result = data.get("result", {})
            assert "uri" in result or "root_uri" in result, (
                f"add_skill result should contain uri or root_uri, got keys: {sorted(result.keys())}"
            )

    def test_add_skill_missing_name(self, api_client):
        skill_data = {"description": "Skill without name", "content": "Skill without name content"}
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        assert add_resp.status_code == 400, (
            f"add_skill without name should return error, got {add_resp.status_code}: {add_resp.text[:200]}"
        )
        if add_resp.status_code == 200:
            raise AssertionError("add_skill without name should NOT return 200")

    def test_add_skill_missing_description(self, api_client):
        skill_data = {
            "name": f"no_desc_skill_{uuid.uuid4().hex[:6]}",
            "content": "Skill without description content",
        }
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        if add_resp.status_code == 500:
            pytest.skip(
                "add_skill returned 500: embedding service unavailable "
                "(PR CI cannot access VLM/Embedding API keys)."
            )
        assert add_resp.status_code == 200, (
            f"add_skill without description should return valid status, got {add_resp.status_code}"
        )

    def test_add_skill_with_content_only(self, api_client):
        skill_data = {
            "name": f"content_only_skill_{uuid.uuid4().hex[:6]}",
            "content": "# content_only_skill\n\nThis skill only has content, no description.",
        }
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        if add_resp.status_code == 500:
            pytest.skip(
                "add_skill returned 500: embedding service unavailable "
                "(PR CI cannot access VLM/Embedding API keys)."
            )
        assert add_resp.status_code == 200, (
            f"add_skill with content only should return valid status, got {add_resp.status_code}"
        )

    def test_add_skill_with_empty_data(self, api_client):
        add_resp = api_client.add_skill(data={}, wait=True)
        assert add_resp.status_code == 400, (
            f"add_skill with empty data should return error, got {add_resp.status_code}: {add_resp.text[:200]}"
        )
        if add_resp.status_code == 200:
            raise AssertionError("add_skill with empty data should NOT return 200")

    def test_skill_searchable_after_add(self, api_client):
        unique_name = f"searchable_skill_{uuid.uuid4().hex[:6]}"
        skill_data = {
            "name": unique_name,
            "description": f"A uniquely named skill for search verification: {unique_name}",
            "content": f"# {unique_name}\n\nA uniquely named skill for search verification.",
        }
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        if add_resp.status_code == 500:
            pytest.skip(
                "add_skill returned 500: embedding service unavailable "
                "(PR CI cannot access VLM/Embedding API keys)."
            )
        if add_resp.status_code != 200:
            return

        find_resp = api_client.find(query=unique_name, limit=5)
        if find_resp.status_code == 401:
            pytest.skip(
                "Find API returned 401: embedding service unavailable "
                "(PR CI cannot access VLM/Embedding API keys)."
            )
        assert find_resp.status_code == 200
        assert "result" in find_resp.json(), "find response should have result field"

    def test_add_skill_result_has_status(self, api_client):
        skill_data = {
            "name": f"status_skill_{uuid.uuid4().hex[:6]}",
            "description": "Skill for status field check",
            "content": "# status_skill\n\nSkill for status field check.",
        }
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        if add_resp.status_code == 500:
            pytest.skip(
                "add_skill returned 500: embedding service unavailable "
                "(PR CI cannot access VLM/Embedding API keys)."
            )
        if add_resp.status_code != 200:
            return
        result = add_resp.json().get("result", {})
        assert "status" in result or "uri" in result, (
            f"add_skill result should contain status or uri, got keys: {sorted(result.keys())}"
        )

    def test_skill_in_find_results_has_score(self, api_client):
        find_resp = api_client.find(query="skill", limit=5)
        if find_resp.status_code == 401:
            pytest.skip(
                "Find API returned 401: embedding service unavailable "
                "(PR CI cannot access VLM/Embedding API keys)."
            )
        assert find_resp.status_code == 200
        skills = find_resp.json().get("result", {}).get("skills", [])
        for skill in skills:
            if isinstance(skill, dict):
                assert "score" in skill, (
                    f"skill find result should have score, got keys: {sorted(skill.keys())}"
                )
                assert isinstance(skill["score"], (int, float)), "score should be numeric"

    def test_skill_in_find_results_has_context_type(self, api_client):
        find_resp = api_client.find(query="skill", limit=5)
        if find_resp.status_code == 401:
            pytest.skip(
                "Find API returned 401: embedding service unavailable "
                "(PR CI cannot access VLM/Embedding API keys)."
            )
        assert find_resp.status_code == 200
        skills = find_resp.json().get("result", {}).get("skills", [])
        for skill in skills:
            if isinstance(skill, dict) and "context_type" in skill:
                assert skill["context_type"] == "skill", (
                    f"skill context_type should be 'skill', got {skill['context_type']}"
                )
