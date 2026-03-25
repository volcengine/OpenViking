import json
import uuid


class TestAddSkill:
    def test_add_skill_with_dict(self, api_client):
        try:
            skill_name = f"test-skill-{uuid.uuid4().hex[:8]}"
            skill = {
                "name": skill_name,
                "description": "A test skill for API testing",
                "content": f"""# {skill_name}

A test skill for API testing.

## Parameters
- **query** (string, required): Search query
- **limit** (integer, optional): Max results, default 10
""",
            }

            response = api_client.add_skill(skill, wait=True)
            print(f"\nAdd skill API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Add Skill API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"].get("status") == "success", (
                f"Expected result status 'success', got {data['result'].get('status')}"
            )
            assert "uri" in data["result"], "'uri' field should exist in result"
            assert "name" in data["result"], "'name' field should exist in result"
            assert data["result"]["name"] == skill_name, (
                f"Expected skill name '{skill_name}', got {data['result']['name']}"
            )

        except Exception as e:
            print(f"Error: {e}")
            raise

    def test_add_skill_with_mcp_format(self, api_client):
        try:
            skill_name = f"test-calculator-{uuid.uuid4().hex[:8]}"
            mcp_tool = {
                "name": skill_name.replace("-", "_"),
                "description": "Perform mathematical calculations",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Mathematical expression to evaluate",
                        }
                    },
                    "required": ["expression"],
                },
            }

            response = api_client.add_skill(mcp_tool, wait=True)
            print(f"\nAdd MCP skill API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Add MCP Skill API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"].get("status") == "success", (
                f"Expected result status 'success', got {data['result'].get('status')}"
            )

        except Exception as e:
            print(f"Error: {e}")
            raise
