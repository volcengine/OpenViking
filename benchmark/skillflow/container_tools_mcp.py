"""Expose task-container tools through MCP; no model or host filesystem access."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess

from mcp.server.fastmcp import FastMCP

# Keep complete output in the same container as the task tools. Returning a
# small, JSON-size-bounded preview prevents the host agent from persisting a
# giant result to a host-only path that those tools cannot read.
REMOTE_RUNNER = r"""
import json, os, pathlib, shutil, signal, subprocess, sys, tempfile
request=json.load(sys.stdin)
root=pathlib.Path('/tmp/skillflow-tool-output'); root.mkdir(exist_ok=True)
folder=pathlib.Path(tempfile.mkdtemp(prefix='call-',dir=root))
out=folder/'stdout.txt'; err=folder/'stderr.txt'
with out.open('wb') as stdout, err.open('wb') as stderr:
    process=subprocess.Popen(request['argv'], stdin=subprocess.PIPE if request['stdin'] is not None else subprocess.DEVNULL,
                             stdout=stdout, stderr=stderr, start_new_session=True)
    timed_out=False
    try:
        process.communicate(None if request['stdin'] is None else request['stdin'].encode(), timeout=request['timeout'])
    except subprocess.TimeoutExpired:
        timed_out=True
        try: os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        process.communicate()
def preview(path,budget):
    with path.open('rb') as stream: raw=stream.read(16000)
    text=raw.decode('utf-8',errors='replace')
    lo,hi=0,len(text)
    while lo<hi:
        mid=(lo+hi+1)//2
        if len(json.dumps(text[:mid],ensure_ascii=True))<=budget: lo=mid
        else: hi=mid-1
    return text[:lo], path.stat().st_size>len(raw) or lo<len(text)
stdout,cut_out=preview(out,8000)
stderr,cut_err=preview(err,3000)
result={'exit_code':124 if timed_out else process.returncode,'stdout':stdout,'stderr':stderr}
if timed_out: result['timed_out']=True
if cut_out or cut_err:
    result.update(output_truncated=True,stdout_path=str(out),stderr_path=str(err),
                  stdout_bytes=out.stat().st_size,stderr_bytes=err.stat().st_size,
                  output_note='Full output is inside the task container. Use read with offsets or bash to inspect these paths.')
else: shutil.rmtree(folder)
print(json.dumps(result,ensure_ascii=False))
"""


def execute(container, workdir, argv, *, stdin=None, timeout=120):
    timeout = max(1, min(int(timeout), 300))
    command = [
        "docker",
        "exec",
        "-i",
        "--workdir",
        workdir,
        container,
        "python3",
        "-c",
        REMOTE_RUNNER,
    ]
    try:
        result = subprocess.run(
            command,
            input=json.dumps({"argv": argv, "stdin": stdin, "timeout": timeout}),
            text=True,
            capture_output=True,
            timeout=timeout + 15,
            errors="replace",
        )
        if result.returncode:
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout[:2000],
                "stderr": result.stderr[:4000],
                "transport_error": True,
            }
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {
            "exit_code": 124,
            "stdout": "",
            "stderr": "Container tool transport timed out",
            "transport_error": True,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--workdir", default="/root")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.container):
        raise ValueError("Invalid container identifier")
    server = FastMCP("skillflow-container")

    @server.tool()
    async def bash(command: str, timeout: int = 120) -> dict:
        """Run Bash inside the task container at its configured working directory.

        Use Python, openpyxl, pandas and PDF tools to inspect inputs and create
        requested artifacts. Only task-provided files are available; do not
        access verifier-owned paths (/tests, /solution, /logs/verifier).
        """
        return await asyncio.to_thread(
            execute,
            args.container,
            args.workdir,
            ["bash", "-lc", command],
            timeout=timeout,
        )

    @server.tool()
    async def read(file_path: str, offset: int = 0, limit: int = 20000) -> dict:
        """Read a UTF-8 text file inside the task container; offsets count characters.

        For spreadsheets/PDFs, use bash with Python libraries instead.
        """
        script = (
            "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
            "text=p.read_text(errors='replace'); start=max(0,int(sys.argv[2])); "
            "print(text[start:start+min(20000,max(1,int(sys.argv[3])))])"
        )
        return await asyncio.to_thread(
            execute,
            args.container,
            args.workdir,
            ["python3", "-c", script, file_path, str(offset), str(limit)],
        )

    @server.tool()
    async def write(file_path: str, content: str) -> dict:
        """Write a UTF-8 text file inside the container, creating parent directories."""
        script = (
            "import pathlib,sys,json; path,content=json.load(sys.stdin); "
            "p=pathlib.Path(path); p.parent.mkdir(parents=True,exist_ok=True); "
            "p.write_text(content); print(str(p),len(content),'characters')"
        )
        return await asyncio.to_thread(
            execute,
            args.container,
            args.workdir,
            ["python3", "-c", script],
            # OpenSpace normalizes read/write path aliases to file_path before
            # validating the MCP schema; match that contract explicitly.
            stdin=json.dumps([file_path, content]),
        )

    @server.tool()
    async def ls(path: str = ".") -> dict:
        """List task-container files in a directory."""
        return await asyncio.to_thread(
            execute,
            args.container,
            args.workdir,
            ["ls", "-la", "--", path],
        )

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
