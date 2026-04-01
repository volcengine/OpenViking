"""
SkillsBench OpenClaw Evaluator.

Evaluates OpenClaw's ability to use skills by running tasks from SkillsBench.

Usage:
    # Prepare benchmark data (clone and filter tasks)
    uv run skill_bench_eval.py prepare

    # List available tasks
    uv run skill_bench_eval.py list

    # Run all tasks
    uv run skill_bench_eval.py run --token YOUR_TOKEN

    # Run specific task
    uv run skill_bench_eval.py run --task 3d-scan-calc --token YOUR_TOKEN
"""

import argparse
import csv
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import traceback
from pathlib import Path

SKILLSBENCH_REPO = "https://github.com/benchflow-ai/skillsbench.git"

EXCLUDED_TASKS = {
    "gh-repo-analytics",
    "mhc-layer-impl",
    "pedestrian-traffic-counting",
    "pg-essay-to-audiobook",
    "scheduling-email-assistant",
    "speaker-diarization-subtitles",
    "multilingual-video-dubbing",
    "trend-anomaly-causal-inference",
    "video-filler-word-remover",
    "video-tutorial-indexer",
}

PROJECT_ROOT = Path(__file__).parent.resolve()
BENCH_DATA_DIR = PROJECT_ROOT / "bench_data"
TASKS_DIR = BENCH_DATA_DIR / "tasks"
OPENCLAW_WORKSPACE = Path.home() / ".openclaw" / "workspace"
OPENCLAW_SKILLS_DIR = OPENCLAW_WORKSPACE / "skills"
WORK_DIR = OPENCLAW_WORKSPACE / "bench_work"
OUTPUT_DIR = PROJECT_ROOT / "bench_output"


def safe_rmtree(path: Path) -> bool:
    if not path.exists():
        return True
    try:

        def _onerror(func, p, exc_info):
            try:
                if os.path.isdir(p):
                    os.chmod(p, stat.S_IRWXU)
                else:
                    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass
            try:
                func(p)
            except Exception:
                pass

        shutil.rmtree(path, onerror=_onerror)
        return True
    except Exception:
        return False


def get_available_tasks() -> list[Path]:
    """Get list of available task directories."""
    if not TASKS_DIR.exists():
        return []
    return sorted([d for d in TASKS_DIR.iterdir() if d.is_dir() and d.name not in EXCLUDED_TASKS])


def run_prepare(args: argparse.Namespace) -> None:
    """Prepare benchmark data by cloning SkillsBench and filtering tasks."""
    print("=== Preparing SkillsBench data ===", file=sys.stderr)

    if BENCH_DATA_DIR.exists():
        if args.force:
            print(f"    Removing existing {BENCH_DATA_DIR} (--force)...", file=sys.stderr)
            shutil.rmtree(BENCH_DATA_DIR)
        else:
            print(
                f"    {BENCH_DATA_DIR} already exists. Use --force to re-download.", file=sys.stderr
            )
            tasks_dir = BENCH_DATA_DIR / "tasks"
            if tasks_dir.exists():
                excluded_count = 0
                for task_name in EXCLUDED_TASKS:
                    task_path = tasks_dir / task_name
                    if task_path.exists():
                        shutil.rmtree(task_path)
                        print(f"    [exclude] removed {task_name}", file=sys.stderr)
                        excluded_count += 1

                remaining = [d.name for d in tasks_dir.iterdir() if d.is_dir()]
                print(
                    f"\n    {len(remaining)} tasks available, {excluded_count} excluded.",
                    file=sys.stderr,
                )
                print(f"    Tasks: {', '.join(sorted(remaining))}", file=sys.stderr)
            return

    temp_dir = PROJECT_ROOT / f"temp_skillsbench_{int(time.time())}"

    print(f"    Cloning {SKILLSBENCH_REPO}...", file=sys.stderr)
    print(f"    (this may take a moment...)", file=sys.stderr)

    process = subprocess.Popen(
        ["git", "clone", "--progress", SKILLSBENCH_REPO, str(temp_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    while True:
        if process.stderr is None:
            break
        line = process.stderr.readline()
        if not line and process.poll() is not None:
            break
        if line:
            line = line.strip()
            if line:
                print(f"    [git] {line}", file=sys.stderr)

    if process.returncode != 0:
        print(f"    [error] git clone failed with code {process.returncode}", file=sys.stderr)
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        sys.exit(1)

    print(f"    Extracting tasks directory...", file=sys.stderr)

    src_tasks = temp_dir / "tasks"
    if not src_tasks.exists():
        print(f"    [error] tasks directory not found in cloned repo", file=sys.stderr)
        shutil.rmtree(temp_dir)
        sys.exit(1)

    BENCH_DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_tasks, TASKS_DIR)

    print(f"    Cleaning up temp files...", file=sys.stderr)
    shutil.rmtree(temp_dir)

    excluded_count = 0
    for task_name in EXCLUDED_TASKS:
        task_path = TASKS_DIR / task_name
        if task_path.exists():
            shutil.rmtree(task_path)
            print(f"    [exclude] removed {task_name}", file=sys.stderr)
            excluded_count += 1

    remaining = [d.name for d in TASKS_DIR.iterdir() if d.is_dir()]
    print(
        f"\n    Done! {len(remaining)} tasks available, {excluded_count} excluded.", file=sys.stderr
    )
    print(f"    Tasks: {', '.join(sorted(remaining))}", file=sys.stderr)


def run_list(args: argparse.Namespace) -> None:
    """List available tasks."""
    tasks = get_available_tasks()

    if not tasks:
        print("No tasks found. Run 'prepare' first.", file=sys.stderr)
        return

    print(f"=== Available Tasks ({len(tasks)}) ===", file=sys.stderr)
    for i, task_dir in enumerate(tasks, 1):
        instruction_file = task_dir / "instruction.md"
        has_instruction = instruction_file.exists()
        skills_dir = task_dir / "environment" / "skills"
        has_skills = skills_dir.exists()
        status = (
            f"instruction={'Y' if has_instruction else 'N'} skills={'Y' if has_skills else 'N'}"
        )
        print(f"  {i:3d}. {task_dir.name} [{status}]", file=sys.stderr)


def run_verification(task_dir: Path, work_dir: Path, storage_workspace: Path) -> dict:
    """Run task verification tests. Returns verification result."""
    task_name = task_dir.name
    tests_dir = task_dir / "tests"

    result = {
        "verified": False,
        "passed": False,
        "test_output": None,
        "error": None,
        "test_score": None,
    }

    if not tests_dir.exists():
        result["error"] = "no tests directory"
        result["verified"] = True
        result["passed"] = True
        print(f"    [verify] no tests directory, skipping verification", file=sys.stderr)
        return result

    test_sh = tests_dir / "test.sh"
    # Collect all test_*.py files
    test_py_files = sorted(list(tests_dir.rglob("test_*.py")))
    test_py_files = [f for f in test_py_files if "__pycache__" not in str(f)]

    if not test_sh.exists() and not test_py_files:
        result["error"] = "no test files found"
        result["verified"] = True
        result["passed"] = True
        print(f"    [verify] no test files, skipping verification", file=sys.stderr)
        return result

    print(f"    [verify] running tests...", file=sys.stderr)

    logs_dir = work_dir / "logs" / "verifier"
    logs_dir.mkdir(parents=True, exist_ok=True)

    if tests_dir.exists():
        for item in tests_dir.rglob("*"):
            if not item.is_file():
                continue
            if item.suffix == ".sh":
                continue
            if item.suffix == ".py" and item.name == "test_outputs.py":
                continue
            rel = item.relative_to(tests_dir)
            dest = work_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(item, dest)

    work_dir_relative = work_dir.relative_to(storage_workspace)
    work_dir_str = str(work_dir_relative)
    tests_dir_relative = str(task_dir / "tests")

    if test_py_files:
        try:
            local_test_files = []
            expected_paths = set()
            all_test_files = []

            # 收集所有测试文件内容和路径
            for test_py in test_py_files:
                with open(test_py, "r", encoding="utf-8") as f:
                    test_content = f.read()
                    all_test_files.append((test_py, test_content))
                # 累积所有路径
                expected_paths.update(re.findall(r"""['"](/root/[^'"]+)['"]""", test_content))
                expected_paths.update(re.findall(r"""['"](/app/[^'"]+)['"]""", test_content))

            # 处理所有需要复制的路径
            for full_path in sorted(expected_paths):
                if full_path.endswith("/"):
                    continue
                try:
                    if full_path.startswith("/root/"):
                        rel = Path(full_path).relative_to("/root")
                    else:
                        rel = Path(full_path).relative_to("/app")
                except ValueError:
                    continue
                src = storage_workspace / rel
                dest = work_dir / rel
                if dest.exists():
                    continue
                if src.exists() and src.is_file():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dest))

            def replace_abs_token(text: str, src: str, dst: str) -> str:
                pattern = re.compile(
                    rf"(^|(?<=[\s'\"`(])){re.escape(src)}(?=($|[\s'\"`)\]]))",
                    re.MULTILINE,
                )
                return pattern.sub(lambda m: f"{m.group(1)}{dst}", text)

            def replace_abs_prefix(text: str, src: str, dst: str) -> str:
                pattern = re.compile(
                    rf"(^|(?<=[\s'\"`(])){re.escape(src)}",
                    re.MULTILINE,
                )
                return pattern.sub(lambda m: f"{m.group(1)}{dst}", text)

            def rewrite_test_text(text: str) -> str:
                abs_token_map = {
                    "/root": "",
                    "/app": "",
                    "/workspace": "./workspace",
                    "/output": "./output",
                    "/data": "./data",
                    "/logs": "./logs",
                    "/tests": f"{tests_dir_relative}",
                }
                for src, dst in abs_token_map.items():
                    text = replace_abs_token(text, src, dst)

                abs_prefix_map = {
                    "/root/": "",
                    "/app/": "",
                    "/workspace/": "./workspace/",
                    "/output/": "./output/",
                    "/data/": "./data/",
                    "/logs/": "./logs/",
                    "/tests/": f"{tests_dir_relative}/",
                }
                for src, dst in abs_prefix_map.items():
                    text = replace_abs_prefix(text, src, dst)

                text = text.replace(
                    'sys.path.insert(0, "/tests/src")',
                    f'sys.path.insert(0, "{tests_dir_relative}/src")',
                )
                text = text.replace(
                    "sys.path.insert(0, '/tests/src')",
                    f"sys.path.insert(0, '{tests_dir_relative}/src')",
                )
                text = text.replace(
                    'sys.path.insert(0, "/root/workspace")', f'sys.path.insert(0, "{work_dir_str}")'
                )
                text = text.replace(
                    "sys.path.insert(0, '/root/workspace')", f"sys.path.insert(0, '{work_dir_str}')"
                )
                text = text.replace(
                    'sys.path.insert(0, "/root")', f'sys.path.insert(0, "{work_dir_str}")'
                )
                text = text.replace(
                    "sys.path.insert(0, '/root')", f"sys.path.insert(0, '{work_dir_str}')"
                )
                text = text.replace("cwd='/root'", f"cwd='{work_dir_str}'")
                text = text.replace('cwd="/root"', f'cwd="{work_dir_str}"')
                return text

            if tests_dir.exists():
                for helper_py in tests_dir.rglob("*.py"):
                    if helper_py.name == "test_outputs.py":
                        continue
                    rel = helper_py.relative_to(tests_dir)
                    dest = work_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.exists():
                        shutil.copy2(helper_py, dest)
                    try:
                        helper_text = dest.read_text(encoding="utf-8")
                        rewritten = rewrite_test_text(helper_text)
                        if rewritten != helper_text:
                            dest.write_text(rewritten, encoding="utf-8")
                    except Exception:
                        pass

                # 处理每个测试文件
                for test_py, test_content in all_test_files:
                    rewritten_content = rewrite_test_text(test_content)
                    local_test_py = work_dir / test_py.name
                    with open(local_test_py, "w", encoding="utf-8") as f:
                        f.write(rewritten_content)
                    local_test_files.append(str(local_test_py))

            env = os.environ.copy()
            env["PYTHONPATH"] = str(work_dir)

            test_cmd = [
                "python",
                "-m",
                "pytest",
                *local_test_files,
                "-v",
                "--tb=short",
                "-W",
                "ignore::pytest.PytestCollectionWarning",
                f"--junitxml={logs_dir}/junit.xml",
            ]

            print(f"    [verify] running: pytest test_outputs.py", file=sys.stderr)

            proc_result = subprocess.run(
                test_cmd,
                capture_output=True,
                text=True,
                cwd=str(work_dir),
                env=env,
                timeout=300,
            )

            result["test_output"] = str(proc_result.stdout or "") + str(proc_result.stderr or "")
            result["verified"] = True
            result["passed"] = proc_result.returncode == 0

            summary_text = result["test_output"] or ""
            collected_match = re.search(r"collected\s+(\d+)\s+items", summary_text)
            passed_count = len(re.findall(r"\bPASSED\s+\[", summary_text))
            failed_count = len(re.findall(r"\bFAILED\s+\[", summary_text))
            skipped_count = len(re.findall(r"\bSKIPPED\s+\[", summary_text))
            total_count = int(collected_match.group(1)) if collected_match else None
            if total_count is None and (passed_count or failed_count or skipped_count):
                total_count = passed_count + failed_count + skipped_count
            if total_count:
                score = passed_count / total_count
                result["test_score"] = round(score, 2)

            if result["passed"]:
                print(f"    [verify] PASSED", file=sys.stderr)
            else:
                print(f"    [verify] FAILED", file=sys.stderr)
                if proc_result.stdout:
                    print(f"    [verify stdout] {proc_result.stdout[:500]}", file=sys.stderr)
                if proc_result.stderr:
                    print(f"    [verify stderr] {proc_result.stderr[:500]}", file=sys.stderr)

        except subprocess.TimeoutExpired:
            result["error"] = "test timeout"
            result["verified"] = True
            result["passed"] = False
            print(f"    [verify] TIMEOUT", file=sys.stderr)
        except Exception as e:
            result["error"] = str(e)
            result["verified"] = True
            result["passed"] = False
            print(f"    [verify] ERROR: {e}", file=sys.stderr)
    else:
        result["verified"] = True
        result["passed"] = True
        print(f"    [verify] no pytest file, skipping", file=sys.stderr)

    return result


def run_task(
    task_dir: Path,
    output_base: Path,
    ov_config_path: Path,
    verify_only: bool = False,
) -> dict:
    """Run a single task. Returns result dict."""
    task_name = task_dir.name
    print(f"\n=== Task: {task_name} ===", file=sys.stderr)

    task_output_dir = output_base / task_name
    if not verify_only and task_output_dir.exists():
        shutil.rmtree(task_output_dir)
    task_output_dir.mkdir(parents=True, exist_ok=True)

    response = ""
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # Load existing result if in verify-only mode
    task_output_dir = output_base / task_name
    existing_result_path = task_output_dir / "result.json"
    if verify_only and existing_result_path.exists():
        try:
            with open(existing_result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
            # Keep original values, only update verification
            result["verification"] = None
            result["end_time"] = time.time()
        except Exception:
            # Fallback to new result if existing is invalid
            result = {
                "task": task_name,
                "status": "pending",
                "response": None,
                "usage": {},
                "error": None,
                "verification": None,
                "start_time": time.time(),
                "end_time": None,
            }
    else:
        result = {
            "task": task_name,
            "status": "pending",
            "response": None,
            "usage": {},
            "error": None,
            "verification": None,
            "start_time": time.time(),
            "end_time": None,
        }

    instruction_file = task_dir / "instruction.md"
    if not instruction_file.exists():
        result["status"] = "error"
        result["error"] = "instruction.md not found"
        print(f"    [error] instruction.md not found", file=sys.stderr)
        return result

    task_skills_dir = task_dir / "environment" / "skills"
    session_name = f"cli__chat__{task_name}"

    work_dir = None

    try:
        # Read ov.conf to get storage.workspace path
        with open(ov_config_path, "r", encoding="utf-8") as f:
            ov_config = json.load(f)
        storage_workspace = Path(ov_config["storage"]["workspace"])

        # Copy skills to target directory
        target_session_dir = storage_workspace / "bot" / session_name
        target_skills_dir = target_session_dir / "skills"
        if not verify_only:
            if target_session_dir.exists():
                safe_rmtree(target_session_dir)
            target_session_dir.mkdir(parents=True, exist_ok=True)
        work_dir = target_session_dir
        if task_skills_dir.exists():
            shutil.copytree(task_skills_dir, target_skills_dir, dirs_exist_ok=True)
            print(f"    [skills] copied to {target_skills_dir}", file=sys.stderr)

        # Copy other environment files except Dockerfile
        env_dir = task_dir / "environment"
        if env_dir.exists():
            for item in env_dir.iterdir():
                if item.name == "skills" or item.name == "Dockerfile" or item.name == ".DS_Store":
                    continue
                target_path = target_session_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, target_path, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target_path)
                print(f"    [env] copied {item.name} to {target_session_dir}", file=sys.stderr)

        # Rewrite instruction paths: remove /root/ prefix
        with open(instruction_file, "r", encoding="utf-8") as f:
            instruction = f.read()

        instruction = re.sub(r"(^|(?<=[\s\'\"`(]))/root/", r"\1/", instruction)

        # Write modified content to session directory, avoid modifying original file
        modified_instruction_path = target_session_dir / "instruction.md"
        with open(modified_instruction_path, "w", encoding="utf-8") as f:
            f.write(instruction)
        print(
            f"    [updated] modified instruction saved to {modified_instruction_path}",
            file=sys.stderr,
        )

        if not verify_only:
            # Run vikingbot command
            print(f"    [running] vikingbot chat...", file=sys.stderr)
            cmd = [
                "vikingbot",
                "chat",
                "-m",
                instruction,
                "-e",
                "-s",
                session_name,
                "-c",
                str(ov_config_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2400)
            if proc.returncode != 0:
                raise Exception(f"vikingbot failed: {proc.stderr}")
            # Parse JSON response
            try:
                raw_output = proc.stdout.strip()
                print(f"    [output] vikingbot output: {raw_output}", file=sys.stderr)
                if not raw_output:
                    raise Exception("vikingbot returned empty output")

                # First try standard JSON parse
                try:
                    bot_result = json.loads(raw_output, strict=False)
                except json.JSONDecodeError:
                    # Fallback: regex extraction for unescaped quotes in text field
                    # Extract text content
                    text_pattern = re.compile(r'"text"\s*:\s*"((?:\\.|[^"\\])*)"', re.DOTALL)
                    text_match = text_pattern.search(raw_output)
                    # Extract token usage
                    token_pattern = re.compile(r'"token_usage"\s*:\s*({[^}]*})', re.DOTALL)
                    token_match = token_pattern.search(raw_output)

                    if not text_match:
                        raise Exception("Failed to extract text from invalid JSON response")

                    text = text_match.group(1).replace('\\"', '"')
                    token_usage = {}
                    if token_match:
                        try:
                            token_usage = json.loads(token_match.group(1), strict=False)
                        except Exception:
                            pass

                    bot_result = {"text": text, "token_usage": token_usage}

                response = bot_result["text"]
                token_usage = bot_result.get("token_usage", {})
                usage = {
                    "input_tokens": token_usage.get("prompt_tokens", 0),
                    "output_tokens": token_usage.get("completion_tokens", 0),
                    "total_tokens": token_usage.get("total_tokens", 0),
                }
            except Exception as e:
                raise Exception(
                    f"Failed to parse vikingbot response: {str(e)}\nRaw output: {proc.stdout}"
                )

            result["status"] = "completed"
            result["response"] = response
            result["usage"] = usage

        if not verify_only:
            with open(task_output_dir / "response.txt", "w", encoding="utf-8") as f:
                f.write(response)
            print(
                f"    [saved] response.txt -> {task_output_dir.name}/response.txt", file=sys.stderr
            )

            preview = response.replace("\n", " | ")[:100]
            print(
                f"    [response] {preview}{'...' if len(response) > 100 else ''}", file=sys.stderr
            )
            print(
                f"    [tokens] in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}",
                file=sys.stderr,
            )
        else:
            # Verify only mode, set default values
            result["status"] = "completed"
            result["response"] = ""
            result["usage"] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        if work_dir:
            verification_result = run_verification(task_dir, work_dir, storage_workspace)
            result["verification"] = verification_result

            with open(task_output_dir / "verification.txt", "w", encoding="utf-8") as f:
                f.write(verification_result.get("test_output", ""))
            print(
                f"    [saved] verification.txt -> {task_output_dir.name}/verification.txt",
                file=sys.stderr,
            )

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        traceback.print_exc(file=sys.stderr)
        print(f"    [error] {e}", file=sys.stderr)
    finally:
        result["end_time"] = time.time()

    with open(task_output_dir / "result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"    [saved] result.json -> {task_output_dir.name}/result.json", file=sys.stderr)

    return result


def run_run(args: argparse.Namespace) -> None:
    """Run benchmark tasks."""
    tasks = get_available_tasks()

    if not tasks:
        print("No tasks found. Run 'prepare' first.", file=sys.stderr)
        sys.exit(1)

    if args.task and (args.count is not None or args.start is not None or args.end is not None):
        print("Error: --task cannot be combined with --count/--start/--end", file=sys.stderr)
        sys.exit(1)
    if args.count is not None and (args.start is not None or args.end is not None):
        print("Error: --count cannot be combined with --start/--end", file=sys.stderr)
        sys.exit(1)

    if args.task:
        task_dir = TASKS_DIR / args.task
        if not task_dir.exists():
            print(f"Task not found: {args.task}", file=sys.stderr)
            sys.exit(1)
        tasks = [task_dir]
    elif args.start is not None or args.end is not None:
        start = args.start or 1
        end = args.end or len(tasks)
        if start < 1 or end < 1 or start > end:
            print(f"Error: invalid range --start {start} --end {end}", file=sys.stderr)
            sys.exit(1)
        if start > len(tasks):
            print(f"Error: --start {start} exceeds available tasks ({len(tasks)})", file=sys.stderr)
            sys.exit(1)
        end = min(end, len(tasks))
        tasks = tasks[start - 1 : end]
    elif args.count:
        tasks = tasks[: args.count]

    output_base = PROJECT_ROOT / "result"
    output_base.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    # Skip tasks already present in result.csv
    completed_tasks = set()
    csv_file = output_base / "result.csv"
    if csv_file.exists():
        try:
            with open(csv_file, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if "taskname" in row:
                        completed_tasks.add(row["taskname"])
        except Exception as e:
            print(f"    [warn] failed to read existing result.csv: {e}", file=sys.stderr)

    # Filter out already completed tasks (skip in verify-only mode)
    original_task_count = len(tasks)
    if not args.verify_only:
        tasks = [t for t in tasks if t.name not in completed_tasks]
    skipped_count = original_task_count - len(tasks)

    if skipped_count > 0:
        print(f"    [info] skipped {skipped_count} already completed tasks", file=sys.stderr)
    if not tasks:
        print("    [info] no new tasks to run, exiting", file=sys.stderr)
        return

    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    print(f"=== Running {len(tasks)} task(s) ===", file=sys.stderr)
    print(f"    output: {output_base}", file=sys.stderr)

    summary_file = output_base / "summary.json"
    for task_dir in tasks:
        result = run_task(
            task_dir=task_dir,
            output_base=output_base,
            ov_config_path=Path(args.ov_config_path),
            verify_only=args.verify_only,
        )

        # Accumulate usage
        current_usage = result.get("usage") or {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        for k in total_usage:
            total_usage[k] += current_usage[k]

        # Update result.csv
        verification = result.get("verification") or {}
        current_score = (result.get("verification") or {}).get("test_score") or 0
        current_row = [
            task_dir.name,
            result["status"],
            current_usage["input_tokens"],
            current_usage["output_tokens"],
            current_usage["total_tokens"],
            result.get("error", ""),
            str(verification.get("verified", False)),
            str(verification.get("passed", False)),
            current_score,
            round(result["end_time"] - result["start_time"], 2),
        ]

        if not args.verify_only:
            # Normal mode: append new row
            csv_exists = csv_file.exists()
            with open(csv_file, "a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if not csv_exists:
                    # Write header
                    writer.writerow(
                        [
                            "taskname",
                            "status",
                            "input_tokens",
                            "output_tokens",
                            "total_tokens",
                            "error",
                            "verified",
                            "passed",
                            "test_score",
                            "cost_time",
                        ]
                    )
                writer.writerow(current_row)
        else:
            # Verify only mode: update existing row
            if csv_file.exists():
                rows = []
                with open(csv_file, "r", encoding="utf-8", newline="") as f:
                    reader = csv.reader(f)
                    header = next(reader)
                    rows.append(header)
                    for row in reader:
                        if row and row[0] == task_dir.name:
                            # Update only verification related columns (indices 6,7,8)
                            row[6] = str(verification.get("verified", False))
                            row[7] = str(verification.get("passed", False))
                            row[8] = str(current_score)
                        rows.append(row)
                # Write back all rows
                with open(csv_file, "w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)

    # Generate final summary from result.csv
    summary_file = output_base / "summary.json"
    final_summary = {
        "total_tasks": 0,
        "completed": 0,
        "passed": 0,
        "errors": 0,
        "total_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "tasks": [],
        "pass_rate": 0.0,
        "score": 0.0,
    }
    all_scores = []
    if csv_file.exists():
        try:
            with open(csv_file, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    final_summary["total_tasks"] += 1
                    final_summary["tasks"].append(row["taskname"])
                    if row["status"] == "completed":
                        final_summary["completed"] += 1
                    if row["status"] == "error":
                        final_summary["errors"] += 1
                    # Accumulate tokens
                    final_summary["total_usage"]["input_tokens"] += int(row.get("input_tokens", 0))
                    final_summary["total_usage"]["output_tokens"] += int(
                        row.get("output_tokens", 0)
                    )
                    final_summary["total_usage"]["total_tokens"] += int(row.get("total_tokens", 0))
                    # Count passed and collect scores
                    if row.get("passed") == "True":
                        final_summary["passed"] += 1
                    if row.get("test_score"):
                        all_scores.append(float(row["test_score"]))
            # Calculate pass rate and total score
            if final_summary["total_tasks"] > 0:
                final_summary["pass_rate"] = round(
                    final_summary["passed"] / final_summary["total_tasks"], 2
                )
            if all_scores:
                final_summary["score"] = round(sum(all_scores), 2)
            # Save summary to file
            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(final_summary, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"    [warn] failed to generate summary from result.csv: {e}", file=sys.stderr)

    print(f"\n=== Summary ===", file=sys.stderr)
    print(
        f"    Completed: {final_summary['completed']}/{final_summary['total_tasks']}",
        file=sys.stderr,
    )
    print(f"    Errors: {final_summary['errors']}", file=sys.stderr)
    print(
        f"    Total tokens: in={final_summary['total_usage']['input_tokens']} out={final_summary['total_usage']['output_tokens']}",
        file=sys.stderr,
    )
    print(f"    Results saved to: {output_base}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="SkillsBench OpenClaw Evaluator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    prepare_parser = subparsers.add_parser("prepare", help="Prepare benchmark data")
    prepare_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force re-download even if data already exists",
    )

    run_parser = subparsers.add_parser("run", help="Run benchmark tasks")
    run_parser.add_argument(
        "--task",
        default=None,
        help="Run specific task only",
    )
    run_parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Run first N tasks only",
    )
    run_parser.add_argument(
        "--start",
        type=int,
        default=None,
        help="Run tasks starting from this index (1-based, same order as list)",
    )
    run_parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="Run tasks ending at this index (inclusive, 1-based, same order as list)",
    )
    run_parser.add_argument(
        "--verify-only",
        action="store_true",
        default=False,
        help="Only run verification step for already executed tasks",
    )
    run_parser.add_argument(
        "--ov-config-path",
        default=str(Path.home() / ".openviking" / "ov.conf"),
        help="Path to OpenViking configuration file",
    )

    args = parser.parse_args()

    if args.command == "prepare":
        run_prepare(args)
    elif args.command == "list":
        run_list(args)
    elif args.command == "run":
        run_run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
