"""
End-to-end LoCoMo ingest via Claude Code chat (stream-json multi-turn).

One `claude -p` subprocess per LoCoMo session. All speaker turns are streamed
through stdin one at a time. Stdin close at end triggers SessionEnd hook
(plugin's session-end.mjs commits once). Plugin's Stop hook fires per-turn
and auto-capture incrementally pushes addMessages per turn.

Result alignment with SDK import_to_ov.py:
  - addMessage granularity: 1 per LoCoMo speaker turn (matches SDK)
  - commit granularity:     1 per LoCoMo session at SessionEnd (matches SDK)
  - extract input:          all 30 messages of a session as one batch (matches SDK)

Stream-json keeps the CC session alive across multiple turns, so SessionEnd
fires only once per LoCoMo session — the only path that aligns BOTH
granularity dimensions without modifying the upstream plugin.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

from ingest import (
    format_locomo_message,
    is_already_ingested,
    load_ingest_record,
    load_locomo_data,
    mark_ingested,
    save_ingest_record,
    write_error_log,
    write_success_csv,
)

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DATA_PATH = str(SCRIPT_DIR / ".tmp" / "locomo10.json")


def _build_env(
    home_dir: str,
    api_key: Optional[str],
    auth_token: Optional[str],
    api_url: Optional[str],
    ov_config: Optional[str],
    ov_cli_config: Optional[str],
    ov_agent_id: Optional[str],
) -> dict:
    env = os.environ.copy()
    env["HOME"] = home_dir
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    if auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = auth_token
    if api_url:
        env["ANTHROPIC_BASE_URL"] = api_url
    if ov_config:
        env["OPENVIKING_CONFIG_FILE"] = ov_config
        env["OPENVIKING_MEMORY_ENABLED"] = "1"
        env["OPENVIKING_DEBUG"] = "1"
    if ov_cli_config:
        env["OPENVIKING_CLI_CONFIG_FILE"] = ov_cli_config
    if ov_agent_id:
        env["OPENVIKING_AGENT_ID"] = ov_agent_id
        env["OPENVIKING_USER"] = ov_agent_id
    env["OPENVIKING_WRITE_PATH_ASYNC"] = "0"
    env.pop("OPENVIKING_URL", None)
    env.pop("OPENVIKING_BASE_URL", None)
    env.pop("OPENVIKING_API_KEY", None)
    env.pop("OPENVIKING_BEARER_TOKEN", None)
    return env


DEFAULT_SYS_PROMPT = (
    "I'll send you messages from a group chat I want you to remember, "
    "one message at a time. Reply with a brief acknowledgement (1-3 words) "
    "so I know you've recorded it. Do not summarize or analyze."
)


def build_session_speaker_prompts(item: dict) -> List[dict]:
    conv = item["conversation"]
    session_keys = sorted(
        [k for k in conv if k.startswith("session_") and not k.endswith("_date_time")],
        key=lambda k: int(k.split("_")[1]),
    )
    out = []
    for sk in session_keys:
        msgs = conv[sk]
        if not isinstance(msgs, list) or not msgs:
            continue
        date_time = conv.get(f"{sk}_date_time", "")
        prompts = []
        for idx, msg in enumerate(msgs):
            line = format_locomo_message(msg)
            if idx == 0:
                # First message includes the dated header so the
                # auto-capture-locomo regex picks up the LoCoMo date for
                # created_at injection.
                text = f"[group chat conversation: {date_time}]\n\n{line}"
            else:
                text = line
            prompts.append(text)
        out.append(
            {
                "sample_id": item["sample_id"],
                "session_key": sk,
                "date_time": date_time,
                "speakers": f"{conv['speaker_a']} & {conv['speaker_b']}",
                "prompts": prompts,
            }
        )
    return out


def _stream_one_session(
    pack: dict,
    project_dir: str,
    env: dict,
    model: Optional[str],
    timeout_sec: int,
    hooks_settings: Optional[str],
    mcp_config: Optional[str],
    sys_prompt: Optional[str],
) -> dict:
    """Open one claude subprocess in stream-json mode, feed all LoCoMo speaker
    turns through stdin, close stdin to trigger SessionEnd, parse output."""
    cmd = [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",  # required for stream-json output
        "--dangerously-skip-permissions",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--strict-mcp-config",
    ]
    if hooks_settings:
        cmd.extend(["--settings", hooks_settings])
    if mcp_config:
        cmd.extend(["--mcp-config", mcp_config])
    if model:
        cmd.extend(["--model", model])
    if sys_prompt:
        cmd.extend(["--append-system-prompt", sys_prompt])

    sample_id = pack["sample_id"]
    session_key = pack["session_key"]
    prompts = pack["prompts"]

    t0 = time.time()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=project_dir,
        env=env,
    )

    # claude stream-json is a request/response protocol: send one message,
    # read stdout until a "result" event arrives, only then send the next.
    # Dumping all messages at once causes claude to process only the first
    # one (or the first that arrived before its read loop saw EOF) and exit.
    stdout_chunks = []
    deadline = time.time() + timeout_sec
    timed_out = False
    try:
        for msg_text in prompts:
            line = json.dumps({"type": "user", "message": {"role": "user", "content": msg_text}})
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
            # Read stdout until we see a "result" event for this turn
            while True:
                if time.time() > deadline:
                    timed_out = True
                    break
                out_line = proc.stdout.readline()
                if not out_line:
                    # stdout closed mid-stream — claude exited unexpectedly
                    break
                stdout_chunks.append(out_line)
                try:
                    d = json.loads(out_line.strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(d, dict) and d.get("type") == "result":
                    break
            if timed_out:
                break
        # Done feeding messages; close stdin so SessionEnd fires.
        try:
            proc.stdin.close()
        except Exception:
            pass
        # Drain remaining stdout/stderr by direct reads — communicate() after
        # an explicit stdin.close() raises "I/O on closed file".
        try:
            for line in proc.stdout:
                stdout_chunks.append(line)
        except Exception:
            pass
        try:
            tail_err = proc.stderr.read() if proc.stderr else ""
        except Exception:
            tail_err = ""
        stderr = tail_err or ""
        try:
            proc.wait(timeout=max(30, deadline - time.time()))
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            proc.wait(timeout=10)
        stdout = "".join(stdout_chunks)
    except (BrokenPipeError, OSError) as e:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "sample_id": sample_id,
            "session_key": session_key,
            "date_time": pack["date_time"],
            "speakers": pack["speakers"],
            "messages_total": len(prompts),
            "messages_ok": 0,
            "messages_err": len(prompts),
            "error": f"stdin write failed: {e}",
            "cc_session_id": "",
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_read_tokens": 0,
            "total_cache_create_tokens": 0,
            "total_num_turns": 0,
            "total_cost_usd": 0,
            "total_duration_ms": int((time.time() - t0) * 1000),
            "stderr_tail": "",
        }

    elapsed_ms = int((time.time() - t0) * 1000)

    # Parse stream-json output: count results, accumulate tokens, find session_id.
    #
    # Important: in stream-json multi-turn mode, each `result` event's
    # `total_cost_usd` is CUMULATIVE across the session (verified: msg1
    # cost=0.127, msg2=0.144, msg3=0.160 — monotonically increasing).
    # But `usage.input_tokens` etc. are PER-TURN (verified: msg1 inp=12923,
    # msg2 inp=2486, msg3 inp=13509 — non-monotonic).
    # So: sum usage across results, take last total_cost_usd.
    cc_session_id = ""
    results_seen = 0
    total_input = total_output = total_cache_read = total_cache_create = 0
    last_total_cost = 0.0
    last_num_turns = 0
    error_results = []

    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        sid = d.get("session_id")
        if sid and not cc_session_id:
            cc_session_id = sid
        if d.get("type") == "result":
            results_seen += 1
            if d.get("is_error"):
                error_results.append(str(d.get("result", ""))[:80])
            u = d.get("usage", {}) or {}
            total_input += u.get("input_tokens", 0) or 0
            total_output += u.get("output_tokens", 0) or 0
            total_cache_read += u.get("cache_read_input_tokens", 0) or 0
            total_cache_create += u.get("cache_creation_input_tokens", 0) or 0
            last_total_cost = d.get("total_cost_usd", 0) or 0
            last_num_turns = d.get("num_turns", 0) or 0

    total_turns = last_num_turns
    total_cost = last_total_cost

    msg_ok = results_seen - len(error_results)
    msg_err = len(prompts) - msg_ok
    return {
        "sample_id": sample_id,
        "session_key": session_key,
        "date_time": pack["date_time"],
        "speakers": pack["speakers"],
        "messages_total": len(prompts),
        "messages_ok": max(msg_ok, 0),
        "messages_err": max(msg_err, 0),
        "error": "; ".join(error_results) if error_results else "",
        "cc_session_id": cc_session_id,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_create_tokens": total_cache_create,
        "total_num_turns": total_turns,
        "total_cost_usd": total_cost,
        "total_duration_ms": elapsed_ms,
        "stderr_tail": (stderr or "")[-300:],
    }


def _poll_until_truly_empty(
    base_url: str = "http://127.0.0.1:1933", report_every: int = 30
) -> None:
    import urllib.request

    last_running = -1
    last_report = 0
    while True:
        try:
            with urllib.request.urlopen(f"{base_url}/api/v1/tasks", timeout=10) as r:
                body = json.loads(r.read())
        except Exception as e:
            print(f"  [poll tasks] {e}, retrying in 30s", file=sys.stderr)
            time.sleep(30)
            continue
        tasks = body.get("result") or []
        running = [t for t in tasks if t.get("status") == "running"]
        if not running:
            print("  [poll tasks] all extract tasks done", file=sys.stderr)
            return
        now = time.time()
        if last_running != len(running) or (now - last_report) > report_every:
            print(f"  [poll tasks] {len(running)} task(s) still running", file=sys.stderr)
            last_running = len(running)
            last_report = now
        time.sleep(20)


def main():
    parser = argparse.ArgumentParser(
        description="LoCoMo e2e ingest via Claude Code stream-json multi-turn"
    )
    parser.add_argument("--input", default=DEFAULT_DATA_PATH)
    parser.add_argument("--sample", default=None)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--record", required=True)
    parser.add_argument("--success-csv", required=True)
    parser.add_argument("--error-log", required=True)
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--auth-token", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Per-session timeout (covers all 30 stream-json turns).",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--hooks-settings", default=None)
    parser.add_argument("--mcp-config", default=None)
    parser.add_argument("--ov-config", default=None)
    parser.add_argument("--ov-cli-config", default=None)
    parser.add_argument("--ov-shared-id", default=None)
    parser.add_argument("--sys-prompt", default=DEFAULT_SYS_PROMPT)
    parser.add_argument("--parallel", type=int, default=1)
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    auth_token = args.auth_token or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    api_url = args.api_url or os.environ.get("ANTHROPIC_BASE_URL")
    if not api_key and not auth_token:
        print("Error: API key required", file=sys.stderr)
        sys.exit(1)

    samples = load_locomo_data(args.input, args.sample)
    print(f"[INFO] Loaded {len(samples)} sample(s)", file=sys.stderr)
    print(f"[INFO] Shared cwd: {args.project_dir}", file=sys.stderr)
    print(f"[INFO] HOME: {args.home}", file=sys.stderr)
    print(f"[INFO] Parallelism: {args.parallel} session(s) at a time", file=sys.stderr)

    os.makedirs(args.project_dir, exist_ok=True)
    os.makedirs(os.path.join(args.home, ".claude"), exist_ok=True)

    record = load_ingest_record(args.record) if not args.force else {}
    ov_agent_id = args.ov_shared_id if (args.ov_shared_id and args.ov_shared_id != "") else None
    env = _build_env(
        args.home, api_key, auth_token, api_url, args.ov_config, args.ov_cli_config, ov_agent_id
    )

    work = []
    for item in samples:
        packs = build_session_speaker_prompts(item)
        for p in packs:
            if not args.force and is_already_ingested(p["sample_id"], p["session_key"], record):
                continue
            work.append(p)

    total_sessions = sum(len(build_session_speaker_prompts(item)) for item in samples)
    print(
        f"[INFO] Total sessions to ingest: {total_sessions}  (already done: {total_sessions - len(work)})",
        file=sys.stderr,
    )
    print(f"[INFO] Total messages to send: {sum(len(p['prompts']) for p in work)}", file=sys.stderr)

    success = error = 0
    sess_done = 0

    def _run(pack):
        return _stream_one_session(
            pack,
            args.project_dir,
            env,
            args.model,
            args.timeout,
            args.hooks_settings,
            args.mcp_config,
            args.sys_prompt,
        )

    if args.parallel <= 1:
        results_iter = (_run(p) for p in work)
    else:
        ex = ThreadPoolExecutor(max_workers=args.parallel)
        futs = [ex.submit(_run, p) for p in work]
        results_iter = (f.result() for f in as_completed(futs))

    for stats in results_iter:
        sess_done += 1
        sample_id = stats["sample_id"]
        session_key = stats["session_key"]
        prefix = f"[{sess_done}/{len(work)}] {sample_id}/{session_key}"

        if stats["messages_ok"] == 0:
            print(
                f"  {prefix} -> all msgs failed: {(stats.get('error', '') or '')[:80]} | stderr: {stats.get('stderr_tail', '')[:150]}",
                file=sys.stderr,
            )
            write_error_log(args.error_log, sample_id, session_key, stats.get("error", ""))
            error += 1
            continue

        print(
            f"  {prefix} -> ok={stats['messages_ok']}/{stats['messages_total']} msgs, "
            f"turns={stats['total_num_turns']}, ${stats['total_cost_usd']:.4f}, "
            f"{stats['total_duration_ms'] / 1000:.1f}s, cc_sid={stats['cc_session_id'][:8]}",
            file=sys.stderr,
        )
        mark_ingested(
            sample_id,
            session_key,
            record,
            {
                "date_time": stats["date_time"],
                "duration_ms": stats["total_duration_ms"],
                "messages_ok": stats["messages_ok"],
                "messages_total": stats["messages_total"],
                "cc_session_id": stats["cc_session_id"],
            },
        )
        save_ingest_record(record, args.record)
        write_success_csv(
            {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "sample_id": sample_id,
                "session": session_key,
                "date_time": stats["date_time"],
                "speakers": stats["speakers"],
                "input_tokens": stats["total_input_tokens"],
                "cache_creation_input_tokens": stats["total_cache_create_tokens"],
                "cache_read_input_tokens": stats["total_cache_read_tokens"],
                "output_tokens": stats["total_output_tokens"],
                "reasoning_tokens": 0,
                "total_cost_usd": stats["total_cost_usd"],
                "duration_ms": stats["total_duration_ms"],
                "response_preview": f"streamjson ok={stats['messages_ok']}/{stats['messages_total']} sid={stats['cc_session_id'][:8]}",
            },
            args.success_csv,
        )
        if stats["messages_err"] > 0:
            write_error_log(
                args.error_log, sample_id, session_key, f"partial: {stats.get('error', '')}"
            )
        success += 1

    if success > 0:
        print(
            "\n[poll tasks] waiting for ALL extract tasks to complete (no timeout)...",
            file=sys.stderr,
        )
        _poll_until_truly_empty()

    print("\n=== Ingest summary ===", file=sys.stderr)
    print(
        f"  Sessions: total={total_sessions}  ok={success}  skip={total_sessions - len(work)}  err={error}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
