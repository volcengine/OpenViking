# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Convert self-hosted MinerU V1 responses to Markdown and image bytes."""

import asyncio
import io
import re
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import httpx

from openviking_cli.utils.config.parser_config import PDFConfig


async def parse_mineru_v1(
    client: httpx.AsyncClient,
    base_url: str,
    path: Path,
    config: PDFConfig,
    health: dict[str, Any],
) -> tuple[str, dict[str, bytes], str | None]:
    """Upload a PDF and read the Markdown result using the advertised V1 contract."""
    features = health.get("features")
    if isinstance(features, dict) and "task_output_formats" in features:
        response, task_id = await _parse_task(client, base_url, path, config)
    else:
        response = await _parse_sync(client, base_url, path, config)
        task_id = response.headers.get("X-MinerU-Task-Id")
        if "zip" not in config.mineru_output_formats:
            result = response.json()
            if result["status"] != "completed":
                raise ValueError("MinerU V1 task did not complete")
            return result["files"][0]["content"]["markdown"], {}, result["task_id"]

    with ZipFile(io.BytesIO(response.content)) as archive:
        markdown = archive.read("markdown.md").decode("utf-8")
        images = {}
        if "zip" in config.mineru_output_formats:
            for entry in archive.infolist():
                # Read direct children as bytes. Archive paths never become output paths.
                name = entry.filename.removeprefix("images/")
                if (
                    entry.filename.startswith("images/")
                    and name not in ("", ".", "..")
                    and not any(separator in name for separator in "/\\:")
                ):
                    images[name] = archive.read(entry)
    return markdown, images, task_id


async def _parse_sync(
    client: httpx.AsyncClient, base_url: str, path: Path, config: PDFConfig
) -> httpx.Response:
    response_format = "zip" if "zip" in config.mineru_output_formats else "json"
    options: dict[str, str | int | list[str]] = {
        "ocr_mode": config.mineru_ocr_mode,
        "output_formats": config.mineru_output_formats,
        "response_format": response_format,
        "wait_timeout": max(1, min(int(config.mineru_timeout), 3600)),
    }
    if config.mineru_tier is not None:
        options["tier"] = config.mineru_tier
    with path.open("rb") as source:
        response = await client.post(
            f"{base_url}/v1/file_parse",
            files={"files": (path.name, source, "application/pdf")},
            params=options,
        )
    response.raise_for_status()
    if response.status_code == 202:
        task_id = _resource_id(response.json()["task_id"])
        while response.status_code == 202:
            await asyncio.sleep(0.2)
            response = await client.get(
                f"{base_url}/v1/tasks/{task_id}/result", params={"response_format": response_format}
            )
            response.raise_for_status()
    return response


async def _parse_task(
    client: httpx.AsyncClient, base_url: str, path: Path, config: PDFConfig
) -> tuple[httpx.Response, str]:
    # MinerU 4.1 advertises task_output_formats and bundles Markdown in "default".
    response = await client.post(
        f"{base_url}/v1/tasks",
        json={
            "tier": config.mineru_tier,
            "ocr_mode": config.mineru_ocr_mode,
            "output_formats": ["default"],
            "files": [{"file_name": path.name, "bytes": path.stat().st_size}],
        },
    )
    response.raise_for_status()
    task = response.json()
    task_id = _resource_id(task["task_id"])
    task_url = f"{base_url}/v1/tasks/{task_id}"
    file = task["files"][0]
    file_id = _resource_id(file["file_id"])

    if file.get("upload"):
        # Use the API's fixed routes so the token and PDF stay on the configured origin.
        uploaded = await client.put(
            f"{task_url}/uploads/{file_id}",
            content=await asyncio.to_thread(path.read_bytes),
            headers={"Content-Type": "application/octet-stream"},
        )
        uploaded.raise_for_status()

    while file["status"] in ("waiting-file", "pending", "running"):
        await asyncio.sleep(0.2)
        response = await client.get(task_url)
        response.raise_for_status()
        file = response.json()["files"][0]

    if file["status"] != "completed" or file["file_id"] != file_id:
        raise ValueError("MinerU V1 task did not complete for the uploaded file")
    response = await client.get(f"{base_url}/v1/files/{file_id}/content")
    response.raise_for_status()
    return response, task_id


def _resource_id(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
        raise ValueError("MinerU returned an invalid task or file ID")
    return value
