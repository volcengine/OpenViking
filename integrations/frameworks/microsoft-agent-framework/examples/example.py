"""Record a preference and recall it in a new MAF conversation."""

import asyncio
import os
import time

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI
from openviking_sdk import AsyncHTTPClient

from openviking_agent_framework import OpenVikingContextProvider


async def main():
    client = AsyncHTTPClient(
        url=os.environ["OPENVIKING_URL"], api_key=os.environ["OPENVIKING_API_KEY"], timeout=60
    )
    await client.initialize()
    try:
        memory = OpenVikingContextProvider(client)
        async with AsyncOpenAI(
            api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ["OPENAI_BASE_URL"]
        ) as model:
            agent = Agent(
                OpenAIChatCompletionClient(model=os.environ["OPENAI_MODEL"], async_client=model),
                instructions="Use retrieved reference data when relevant.",
                context_providers=[memory],
            )
            session = agent.create_session()
            print(
                (await agent.run("Remember my preferred reply format: JSON.", session=session)).text
            )
            job = await memory.commit(session)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                task = await client.get_task(job["task_id"])
                if task is None or task["status"] in {"failed", "cancelled"}:
                    raise RuntimeError(f"Extraction failed: {task}")
                if task["status"] == "completed":
                    break
                await asyncio.sleep(1)
            else:
                raise TimeoutError("Extraction did not complete within 180 seconds")
            print(
                (
                    await agent.run(
                        "What reply format do I prefer?", session=agent.create_session()
                    )
                ).text
            )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
