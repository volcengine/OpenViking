"""Run actual OpenAI, Anthropic and Codex stream parsers against a gateway URL."""

import argparse
import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path


async def probe(base_url, api_key, model="mock-silent", minimum_seconds=39, read_timeout=20):
    """Return parser results; any missing dependency or failed assertion fails the probe."""
    import anthropic
    import openai

    base_url = base_url.rstrip("/")
    expected = "mock response complete"

    async def sdk(kind):
        started = time.monotonic()
        options = {"api_key": api_key, "max_retries": 0, "timeout": read_timeout}
        if kind == "anthropic":
            async with anthropic.AsyncAnthropic(base_url=base_url, **options) as client:
                async with client.messages.stream(
                    model=model, max_tokens=64, messages=[{"role": "user", "content": "Hi"}]
                ) as stream:
                    text = "".join([part async for part in stream.text_stream])
                    message = await stream.get_final_message()
                assert message.stop_reason == "end_turn", message.stop_reason
        else:
            async with openai.AsyncOpenAI(base_url=base_url + "/v1", **options) as client:
                if kind == "openai-chat":
                    stream = await client.chat.completions.create(
                        model=model, messages=[{"role": "user", "content": "Hi"}], stream=True
                    )
                    chunks = [chunk async for chunk in stream]
                    text = "".join(
                        chunk.choices[0].delta.content or "" for chunk in chunks if chunk.choices
                    )
                    assert any(
                        chunk.choices and chunk.choices[0].finish_reason == "stop"
                        for chunk in chunks
                    )
                else:
                    async with client.responses.stream(
                        model=model, input="Hi", store=False
                    ) as stream:
                        async for _ in stream:
                            pass
                        final = await stream.get_final_response()
                    assert final.status == "completed", final.status
                    text = final.output_text
        elapsed = time.monotonic() - started
        assert text == expected, (kind, text)
        assert elapsed >= minimum_seconds, (kind, elapsed, minimum_seconds)
        return {
            "client": kind,
            "status": "passed",
            "text": text,
            "elapsed_seconds": round(elapsed, 3),
        }

    async def codex():
        executable = shutil.which("codex")
        if executable is None:
            raise RuntimeError("codex executable is required for the Codex parser probe")
        started = time.monotonic()
        provider = {
            "name": "Gateway e2e",
            "base_url": base_url + "/v1",
            "env_key": "OV_E2E_CODEX_API_KEY",
            "wire_api": "responses",
            # Codex times parsed events; SSE comments only renew the HTTP byte timeout.
            "stream_idle_timeout_ms": max(60000, int(read_timeout * 1000)),
        }
        provider_toml = (
            "{" + ",".join(f"{key}={json.dumps(value)}" for key, value in provider.items()) + "}"
        )
        with tempfile.TemporaryDirectory(prefix="gateway-sdk-probe-") as directory:
            command = [
                executable,
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "--json",
                "--cd",
                directory,
                "--sandbox",
                "read-only",
                "--model",
                model,
                "--config",
                'model_provider="gateway_e2e"',
                "--config",
                "model_providers.gateway_e2e=" + provider_toml,
                "--config",
                'model_reasoning_effort="low"',
                "Reply briefly without calling any tools.",
            ]
            process = await asyncio.create_subprocess_exec(
                *command,
                env={**os.environ, "OV_E2E_CODEX_API_KEY": api_key},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), max(120, minimum_seconds + 60)
                )
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            if process.returncode:
                raise RuntimeError(f"Codex exit {process.returncode}: {stderr.decode()[-2000:]}")
            events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
            messages = [
                event["item"].get("text", "")
                for event in events
                if event.get("type") == "item.completed"
                and event.get("item", {}).get("type") == "agent_message"
            ]
            assert expected in messages, messages
            assert any(event.get("type") == "turn.completed" for event in events), events
        elapsed = time.monotonic() - started
        assert elapsed >= minimum_seconds, ("codex", elapsed, minimum_seconds)
        return {
            "client": "codex",
            "status": "passed",
            "text": expected,
            "elapsed_seconds": round(elapsed, 3),
            "stream_idle_timeout_seconds": provider["stream_idle_timeout_ms"] / 1000,
        }

    outcomes = await asyncio.gather(
        sdk("openai-chat"),
        sdk("openai-responses"),
        sdk("anthropic"),
        codex(),
        return_exceptions=True,
    )
    labels = ("openai-chat", "openai-responses", "anthropic", "codex")
    results = [
        {"client": label, "status": "failed", "error": f"{type(value).__name__}: {value}"}
        if isinstance(value, BaseException)
        else value
        for label, value in zip(labels, outcomes, strict=True)
    ]
    return {
        "versions": {"openai": openai.__version__, "anthropic": anthropic.__version__},
        "model": model,
        "minimum_seconds": minimum_seconds,
        "read_timeout_seconds": read_timeout,
        "results": results,
        "passed": all(result["status"] == "passed" for result in results),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Gateway origin, without /v1")
    parser.add_argument("--api-key-env", default="OV_TEST_API_KEY")
    parser.add_argument("--model", default="mock-silent")
    parser.add_argument("--minimum-seconds", type=float, default=39)
    parser.add_argument("--read-timeout", type=float, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    key = os.environ.get(args.api_key_env)
    if not key:
        parser.error(f"Set {args.api_key_env} to a gateway test key")
    report = asyncio.run(
        probe(args.base_url, key, args.model, args.minimum_seconds, args.read_timeout)
    )
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
