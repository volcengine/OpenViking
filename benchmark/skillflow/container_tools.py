"""Task-container command transport used by VikingBot benchmark tools."""

from __future__ import annotations

import json
import subprocess

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
