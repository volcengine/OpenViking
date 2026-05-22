import uuid


class TestSkillCrudDeep:
    def test_add_skill_basic(self, api_client):
        skill_data = {
            "name": f"test_skill_{uuid.uuid4().hex[:8]}",
            "description": "A test skill for CRUD validation",
            "content": "# test_skill\n\nA test skill for CRUD validation.",
        }
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        assert add_resp.status_code == 200, (
            f"add_skill should return 200, got {add_resp.status_code}"
        )

    def test_add_skill_result_has_status(self, api_client):
        skill_data = {
            "name": f"status_skill_{uuid.uuid4().hex[:8]}",
            "description": "Skill for status check",
            "content": "# status_skill\n\nSkill for status check.",
        }
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        assert add_resp.status_code == 200
        result = add_resp.json()
        assert result.get("status") == "ok" or "result" in result, (
            f"add_skill response should have status=ok or result, got {result}"
        )

    def test_add_skill_minimal_data(self, api_client):
        add_resp = api_client.add_skill(
            data={"name": f"min_skill_{uuid.uuid4().hex[:8]}", "content": "minimal skill content"},
            wait=True,
        )
        assert add_resp.status_code == 200, (
            f"add_skill with minimal data should return 200, got {add_resp.status_code}"
        )

    def test_add_skill_complex_data(self, api_client):
        skill_data = {
            "name": f"complex_skill_{uuid.uuid4().hex[:8]}",
            "description": "Complex skill with many fields",
            "content": "# complex_skill\n\nComplex skill with many fields including parameters.",
            "category": "advanced",
            "tags": ["test", "validation", "complex"],
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            },
        }
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        assert add_resp.status_code == 200, (
            f"add_skill with complex data should return 200, got {add_resp.status_code}"
        )

    def test_add_skill_duplicate_name(self, api_client):
        name = f"dup_skill_{uuid.uuid4().hex[:8]}"
        skill_data = {
            "name": name,
            "description": "First instance",
            "content": f"# {name}\n\nFirst instance.",
        }

        first = api_client.add_skill(data=skill_data, wait=True)
        assert first.status_code == 200

        skill_data2 = {
            "name": name,
            "description": "Second instance with same name",
            "content": f"# {name}\n\nSecond instance.",
        }
        second = api_client.add_skill(data=skill_data2, wait=True)
        assert second.status_code == 200, (
            f"duplicate skill name should return 200/409/500, got {second.status_code}"
        )

    def test_add_skill_then_find(self, api_client):
        unique_name = f"findable_skill_{uuid.uuid4().hex[:8]}"
        skill_data = {
            "name": unique_name,
            "description": "Skill that should be findable via search",
            "content": f"# {unique_name}\n\nSkill that should be findable via search.",
        }
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        if add_resp.status_code != 200:
            return

        find_resp = api_client.find(query=unique_name, limit=5)
        assert find_resp.status_code == 200
        result = find_resp.json().get("result", {})
        assert isinstance(result, dict), "find result should be dict"

    def test_add_skill_unicode_content(self, api_client):
        skill_data = {
            "name": f"unicode_skill_{uuid.uuid4().hex[:8]}",
            "description": "这是一个中文描述的技能 🚀",
            "content": "# unicode_skill\n\n这是一个中文描述的技能 🚀",
            "category": "国际化测试",
        }
        add_resp = api_client.add_skill(data=skill_data, wait=True)
        assert add_resp.status_code == 200, (
            f"add_skill with unicode should return 200, got {add_resp.status_code}"
        )

    def test_add_skill_async(self, api_client):
        skill_data = {
            "name": f"async_skill_{uuid.uuid4().hex[:8]}",
            "description": "Async skill addition",
            "content": "# async_skill\n\nAsync skill addition.",
        }
        add_resp = api_client.add_skill(data=skill_data, wait=False)
        assert add_resp.status_code == 200, (
            f"add_skill async should return 200, got {add_resp.status_code}"
        )
        result = add_resp.json().get("result", {})
        assert "task_id" in result or "status" in result, (
            f"async add_skill should return task_id or status, got {result}"
        )

    def test_skills_field_in_find(self, api_client):
        find_resp = api_client.find(query="skill", limit=5)
        assert find_resp.status_code == 200
        result = find_resp.json().get("result", {})
        assert "skills" in result, (
            f"find result should contain skills field, got keys: {list(result.keys())}"
        )
