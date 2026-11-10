"""Read OV experience at task entry; baseline never contacts OV."""

import json
from pathlib import Path


async def recall(prompt: str, memory: dict, output: Path) -> tuple[str, dict]:
    """Read trained Experience files once at task entry; baseline never calls OV."""
    if not memory:
        audit = {"enabled": False, "boundary": "task_entry", "matches": []}
        (output / "ov-recall.json").write_text(json.dumps(audit))
        return prompt, audit
    import httpx

    url, root = memory.get("openviking_url"), memory.get("root_uri")
    if not url or not root:
        raise ValueError("Memory-enabled rollout requires OV URL and policy root")
    headers = {}
    for key, header in [
        ("openviking_api_key", "X-API-Key"),
        ("openviking_account", "X-OpenViking-Account"),
        ("openviking_user", "X-OpenViking-User"),
    ]:
        if memory.get(key):
            headers[header] = str(memory[key])
    async with httpx.AsyncClient(base_url=url.rstrip("/"), headers=headers, timeout=120) as client:
        response = await client.post(
            "/api/v1/search/search",
            json={"query": prompt[:16000], "target_uri": root, "limit": 3, "read_content": True},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "ok" or not isinstance(data.get("result"), dict):
            raise RuntimeError("OV retrieval did not return a successful search result")
        result = data["result"]
        if result.get("errors"):
            raise RuntimeError("OV retrieval reported errors")
        matches = result.get("memories", result.get("entries"))
        if not isinstance(matches, list):
            raise RuntimeError("OV retrieval response has no memory list")
        blocks, evidence = [], []
        for row in matches[:3]:
            uri = row.get("uri", "")
            if not uri.startswith(root.rstrip("/") + "/"):
                raise RuntimeError("OV returned memory outside the experiment policy root")
            content = row.get("content")
            if not isinstance(content, str) or not content.strip():
                read = await client.get("/api/v1/content/read", params={"uri": uri})
                read.raise_for_status()
                read_data = read.json()
                if read_data.get("status") != "ok" or not isinstance(read_data.get("result"), str):
                    raise RuntimeError("Retrieved OV memory could not be read")
                content = read_data["result"]
            blocks.append(f"Experience ({uri}):\n{content[:4000]}")
            evidence.append({"uri": uri, "content": content[:4000]})
    audit = {"enabled": True, "boundary": "task_entry", "matches": evidence}
    (output / "ov-recall.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    if blocks:
        prompt += (
            "\n\nPrior training experiences (guidance; check against this task's authoritative rules):\n"
            + "\n\n".join(blocks)
        )
    return prompt, audit
