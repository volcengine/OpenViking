"""
Memory V2 全面端到端测试套件
覆盖所有记忆文件类型：
- preferences (偏好设置) - User scope
- entities (实体信息) - User scope
- events (事件记录) - User scope
- profile (用户画像) - User scope, 单文件
- skills (技能) - Agent scope
- tools (工具) - Agent scope

测试方式：通过 OpenClaw agent 进行对话，然后通过 OV API commit session 触发记忆提取。
OpenClaw Gateway 会自动分配 OV session ID，需要通过对比 sessions 列表来定位。
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from utils.openclaw_cli_client import _wait_for_session_lock_release
from utils.test_utils import SessionIdManager

SERVER_URL = os.environ.get("SERVER_URL", "http://127.0.0.1:1933")
OPENVIKING_API_KEY = os.environ.get("OPENVIKING_API_KEY", "test-root-api-key")
TASK_POLL_INTERVAL = 5
TASK_POLL_MAX_WAIT = 120


def _get_api_headers() -> Dict[str, str]:
    return {
        "X-OpenViking-API-Key": OPENVIKING_API_KEY,
        "Content-Type": "application/json",
    }


def get_viking_data_dir() -> Path:
    """动态获取 viking 数据目录路径（user scope memories）"""
    if os.environ.get("VIKING_DATA_DIR"):
        return (
            Path(os.environ["VIKING_DATA_DIR"])
            / "viking"
            / "default"
            / "user"
            / "default"
            / "memories"
        )

    project_root = Path(__file__).parent.parent.parent.parent.parent
    project_data_dir = (
        project_root / ".openviking_data" / "viking" / "default" / "user" / "default" / "memories"
    )
    if project_data_dir.exists():
        return project_data_dir

    config_candidates = [
        project_root / "ov.conf.temp",
        project_root / "ov.conf",
        Path.home() / ".openviking" / "ov.conf",
    ]
    for config_path in config_candidates:
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                workspace = config.get("storage", {}).get("workspace") or config.get(
                    "agfs", {}
                ).get("path")
                if workspace:
                    return Path(workspace) / "viking" / "default" / "user" / "default" / "memories"
            except Exception:
                pass

    cwd_data_dir = (
        Path.cwd() / ".openviking_data" / "viking" / "default" / "user" / "default" / "memories"
    )
    if cwd_data_dir.exists():
        return cwd_data_dir

    return Path.home() / ".openviking_data" / "viking" / "default" / "user" / "default" / "memories"


class OpenVikingAPIClient:
    """OV API 客户端"""

    def __init__(self, server_url: str = SERVER_URL, api_key: str = OPENVIKING_API_KEY):
        self.server_url = server_url
        self.api_key = api_key
        self.headers = _get_api_headers()

    def list_session_ids(self) -> Set[str]:
        """获取当前所有 OV session ID 集合"""
        try:
            resp = requests.get(
                f"{self.server_url}/api/v1/sessions",
                headers=self.headers,
                timeout=10,
            )
            if resp.status_code != 200:
                return set()
            sessions = resp.json().get("result", [])
            return {s["session_id"] for s in sessions}
        except Exception:
            return set()

    def find_new_session_id(self, before_ids: Set[str]) -> Optional[str]:
        """对比前后 session 列表，找到新创建的 session ID"""
        after_ids = self.list_session_ids()
        new_ids = after_ids - before_ids
        if new_ids:
            return new_ids.pop()
        return None

    def find_session_with_most_messages(
        self, candidate_ids: Optional[Set[str]] = None
    ) -> Optional[str]:
        """在候选 session 中找到消息数最多的 session"""
        try:
            resp = requests.get(
                f"{self.server_url}/api/v1/sessions",
                headers=self.headers,
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            sessions = resp.json().get("result", [])
            best_id = None
            best_pending = -1
            for s in sessions:
                sid = s["session_id"]
                if candidate_ids and sid not in candidate_ids:
                    continue
                try:
                    detail_resp = requests.get(
                        f"{self.server_url}/api/v1/sessions/{sid}",
                        headers=self.headers,
                        timeout=5,
                    )
                    if detail_resp.status_code == 200:
                        pending = detail_resp.json().get("result", {}).get("pending_tokens", 0)
                        if pending > best_pending:
                            best_pending = pending
                            best_id = sid
                except Exception:
                    continue
            return best_id
        except Exception:
            return None

    def commit_session(self, session_id: str) -> Dict[str, Any]:
        resp = requests.post(
            f"{self.server_url}/api/v1/sessions/{session_id}/commit",
            headers=self.headers,
            timeout=30,
        )
        return {"status_code": resp.status_code, "data": resp.json() if resp.text else {}}

    def get_task(self, task_id: str) -> Dict[str, Any]:
        resp = requests.get(
            f"{self.server_url}/api/v1/tasks/{task_id}",
            headers=self.headers,
            timeout=10,
        )
        return {"status_code": resp.status_code, "data": resp.json() if resp.text else {}}

    def poll_task_until_done(
        self, task_id: str, max_wait: int = TASK_POLL_MAX_WAIT
    ) -> Dict[str, Any]:
        start = time.time()
        while time.time() - start < max_wait:
            task_resp = self.get_task(task_id)
            if task_resp["status_code"] != 200:
                time.sleep(TASK_POLL_INTERVAL)
                continue
            task_data = task_resp["data"].get("result", {})
            status = task_data.get("status", "unknown")
            if status in ("completed", "failed"):
                return task_data
            time.sleep(TASK_POLL_INTERVAL)
        return {"status": "timeout", "task_id": task_id}


class MemoryV2TestSuite:
    """Memory V2 全面测试套件 - OpenClaw 对话 + OV API commit"""

    def __init__(self):
        self.test_scenarios = self._create_test_scenarios()
        self.viking_data_dir = get_viking_data_dir()
        self.api = OpenVikingAPIClient()

    def _create_test_scenarios(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "preferences",
                "description": "测试偏好设置记忆",
                "test_message": "我喜欢用Python编程，偏好使用VS Code编辑器，喜欢喝咖啡，特别是美式咖啡",
                "memory_type": "preferences",
            },
            {
                "name": "entities",
                "description": "测试实体信息记忆",
                "test_message": "我叫李明，今年28岁，是一名软件工程师，在字节跳动工作，住在北京海淀区",
                "memory_type": "entities",
            },
            {
                "name": "events",
                "description": "测试事件记录记忆",
                "test_message": "昨天我参加了公司的技术分享会，主题是微服务架构，会议持续了2小时，有50多人参加",
                "memory_type": "events",
            },
            {
                "name": "profile",
                "description": "测试用户画像记忆",
                "test_message": "我是一名技术负责人，有10年开发经验，专注于后端架构设计，喜欢用Python和Go语言",
                "memory_type": "profile",
            },
            {
                "name": "skills",
                "description": "测试技能记忆",
                "test_message": "我擅长使用Docker进行容器化部署，熟练掌握Kubernetes集群管理，有丰富的CI/CD流水线搭建经验",
                "memory_type": "skills",
            },
            {
                "name": "tools",
                "description": "测试工具记忆",
                "test_message": "我经常使用Git进行版本控制，用Jenkins做持续集成，用Prometheus和Grafana监控服务",
                "memory_type": "tools",
            },
        ]

    def run_openclaw_command(self, message: str, session_id: str) -> Dict[str, Any]:
        """执行 openclaw agent 命令"""
        _wait_for_session_lock_release(session_id)

        cmd = [
            "openclaw",
            "agent",
            "--session-id",
            session_id,
            "--message",
            message,
            "--json",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        _wait_for_session_lock_release(session_id)

        if result.returncode != 0:
            raise Exception(f"OpenClaw command failed: {result.stderr}")

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"raw_output": result.stdout}

    def _snapshot_all_memory_files(self) -> Dict[str, float]:
        """记录所有记忆目录的文件快照（路径 → mtime）"""
        files: Dict[str, float] = {}

        # user scope: preferences, entities, events, profile
        for md_file in self.viking_data_dir.rglob("*.md"):
            files[str(md_file)] = md_file.stat().st_mtime

        # agent scope: skills, tools
        agent_base_dir = self.viking_data_dir.parent.parent.parent / "agent"
        if agent_base_dir.exists():
            for md_file in agent_base_dir.rglob("*.md"):
                files[str(md_file)] = md_file.stat().st_mtime

        return files

    def check_memory_files(
        self, memory_type: str, before_files: Dict[str, float]
    ) -> Dict[str, Any]:
        """检查 commit 后是否有新增或修改的记忆文件（全目录扫描）"""
        result = {
            "memory_type": memory_type,
            "found": False,
            "new_files": [],
            "modified_files": [],
            "all_files": [],
        }

        # 先检查目标类型目录
        target_result = self._check_target_type(memory_type, before_files)
        result["new_files"].extend(target_result["new_files"])
        result["modified_files"].extend(target_result["modified_files"])
        result["all_files"].extend(target_result["all_files"])

        if target_result["found"]:
            result["found"] = True
            return result

        # 目标目录无变化，扫描全目录查找新增/修改的文件
        print(f"  ⚠ {memory_type} 目录无直接变化，扫描全目录...")
        after_files = self._snapshot_all_memory_files()

        for file_str, new_mtime in after_files.items():
            if file_str not in before_files:
                result["found"] = True
                result["new_files"].append(file_str)
                rel_path = self._relative_display(file_str)
                print(f"  ✓ 其他目录新增文件: {rel_path}")
            else:
                old_mtime = before_files[file_str]
                if new_mtime > old_mtime:
                    result["found"] = True
                    result["modified_files"].append(file_str)
                    rel_path = self._relative_display(file_str)
                    print(f"  ✓ 其他目录文件已更新: {rel_path}")

        if not result["found"]:
            print("  ✗ 全目录扫描均无新增或修改的记忆文件")
        return result

    def _relative_display(self, file_str: str) -> str:
        """生成相对路径用于显示"""
        try:
            base = self.viking_data_dir.parent.parent.parent
            return str(Path(file_str).relative_to(base))
        except ValueError:
            return Path(file_str).name

    def _check_target_type(
        self, memory_type: str, before_files: Dict[str, float]
    ) -> Dict[str, Any]:
        """检查目标 memory_type 目录的文件变化"""
        result: Dict[str, Any] = {
            "found": False,
            "new_files": [],
            "modified_files": [],
            "all_files": [],
        }

        if memory_type == "profile":
            profile_file = self.viking_data_dir / "profile.md"
            if profile_file.exists():
                file_str = str(profile_file)
                result["all_files"].append(file_str)
                if file_str not in before_files:
                    result["found"] = True
                    result["new_files"].append(file_str)
                    print(f"  ✓ 新增 profile 文件: {profile_file}")
                else:
                    old_mtime = before_files[file_str]
                    new_mtime = profile_file.stat().st_mtime
                    if new_mtime > old_mtime:
                        result["found"] = True
                        result["modified_files"].append(file_str)
                        print("  ✓ profile 文件已更新 (mtime 变化)")
                    else:
                        print(f"  ⚠ profile 文件未变化: {profile_file}")
            else:
                print(f"  ✗ profile 文件不存在: {profile_file}")
            return result

        if memory_type in ["skills", "tools"]:
            agent_base_dir = self.viking_data_dir.parent.parent.parent / "agent"
            if not agent_base_dir.exists():
                print(f"  ✗ agent 目录不存在: {agent_base_dir}")
                return result

            search_types = [memory_type]
            if memory_type == "tools":
                search_types = ["tools", "skills"]

            for agent_space_dir in sorted(agent_base_dir.iterdir()):
                if not agent_space_dir.is_dir() or agent_space_dir.name.startswith("."):
                    continue
                for search_type in search_types:
                    memory_dir = agent_space_dir / "memories" / search_type
                    if not memory_dir.exists():
                        continue

                    for md_file in sorted(memory_dir.glob("*.md")):
                        file_str = str(md_file)
                        result["all_files"].append(file_str)
                        if file_str not in before_files:
                            result["found"] = True
                            result["new_files"].append(file_str)
                            print(f"  ✓ 新增记忆文件: {md_file.name} (在 {search_type}/ 目录下)")
                        else:
                            old_mtime = before_files[file_str]
                            new_mtime = md_file.stat().st_mtime
                            if new_mtime > old_mtime:
                                result["found"] = True
                                result["modified_files"].append(file_str)
                                print(
                                    f"  ✓ 记忆文件已更新: {md_file.name} (在 {search_type}/ 目录下)"
                                )

            if not result["found"]:
                if result["all_files"]:
                    print(f"  ⚠ {memory_type} 目录有文件但均无变化")
                else:
                    print(f"  ✗ 未找到 {memory_type} 相关记忆文件")
            return result

        # user scope: preferences, entities, events
        memory_dir = self.viking_data_dir / memory_type
        if not memory_dir.exists():
            print(f"  ✗ 记忆目录不存在: {memory_dir}")
            return result

        for md_file in sorted(memory_dir.rglob("*.md")):
            file_str = str(md_file)
            result["all_files"].append(file_str)
            if file_str not in before_files:
                result["found"] = True
                result["new_files"].append(file_str)
                rel = md_file.relative_to(memory_dir)
                print(f"  ✓ 新增记忆文件: {rel}")
            else:
                old_mtime = before_files[file_str]
                new_mtime = md_file.stat().st_mtime
                if new_mtime > old_mtime:
                    result["found"] = True
                    result["modified_files"].append(file_str)
                    rel = md_file.relative_to(memory_dir)
                    print(f"  ✓ 记忆文件已更新: {rel}")

        if not result["found"]:
            if result["all_files"]:
                print(f"  ⚠ {memory_type} 目录有文件但均无变化")
            else:
                print(f"  ✗ 未找到 {memory_type} 记忆文件")
        return result

    def test_single_scenario(self, scenario: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """测试单个场景：OpenClaw 对话 → OV API commit → 轮询任务 → 检查记忆文件"""
        print(f"\n{'=' * 60}")
        print(f"测试场景: {scenario['name']} - {scenario['description']}")
        print(f"{'=' * 60}")

        result = {
            "scenario": scenario["name"],
            "description": scenario["description"],
            "memory_type": scenario["memory_type"],
            "steps": {},
        }

        try:
            # 每个场景使用独立的 session ID，确保 OpenClaw 创建新的 OV session
            scenario_session_id = SessionIdManager.generate_session_id(
                prefix=f"memv2_{scenario['name']}"
            )

            # 步骤 1: 记录当前 OV sessions 快照和全目录记忆文件快照
            print("\n[步骤 1/5] 记录快照")
            before_session_ids = self.api.list_session_ids()
            before_memory_files = self._snapshot_all_memory_files()
            print(f"  当前 session 数量: {len(before_session_ids)}")
            print(f"  当前全目录记忆文件数量: {len(before_memory_files)}")
            print(f"  场景 session ID: {scenario_session_id}")
            result["steps"]["snapshot"] = "success"

            # 步骤 2: 通过 OpenClaw 发送消息
            print("\n[步骤 2/5] 通过 OpenClaw 发送消息")
            print(f"  消息: {scenario['test_message'][:50]}...")
            self.run_openclaw_command(scenario["test_message"], scenario_session_id)
            print("✓ OpenClaw 对话完成")
            result["steps"]["openclaw_chat"] = "success"
            time.sleep(2)

            # 步骤 3: 找到 OV session 并 commit
            print("\n[步骤 3/5] 查找 OV session 并 commit")
            ov_session_id = self.api.find_new_session_id(before_session_ids)
            if not ov_session_id:
                print("  ⚠ 未找到新 session，在已有 session 中查找消息最多的")
                ov_session_id = self.api.find_session_with_most_messages()

            if not ov_session_id:
                print("  ✗ 无可用 session")
                result["steps"]["commit"] = "failed"
                result["status"] = "error"
                result["error"] = "未找到 OV session"
                return False, result

            print(f"  OV session ID: {ov_session_id}")
            commit_resp = self.api.commit_session(ov_session_id)
            commit_data = commit_resp.get("data", {})
            commit_result = commit_data.get("result", {})
            task_id = commit_result.get("task_id")

            if commit_resp["status_code"] == 200 and task_id:
                print(f"✓ Commit 成功 (task_id: {task_id})")
                result["steps"]["commit"] = "success"
                result["ov_session_id"] = ov_session_id
                result["task_id"] = task_id
            else:
                print(f"✗ Commit 失败: {commit_resp}")
                result["steps"]["commit"] = "failed"
                result["status"] = "failed"
                result["error"] = f"Commit 失败: {commit_resp}"
                return False, result

            # 步骤 4: 轮询任务直到完成
            print(f"\n[步骤 4/5] 等待记忆提取完成 (轮询 task_id: {task_id})")
            task_result = self.api.poll_task_until_done(task_id)
            task_status = task_result.get("status", "unknown")
            memories_extracted = task_result.get("result", {}).get("memories_extracted", {})
            token_usage = task_result.get("result", {}).get("token_usage", {})

            print(f"  任务状态: {task_status}")
            print(f"  提取的记忆: {memories_extracted}")
            if token_usage:
                llm_tokens = token_usage.get("llm", {}).get("total_tokens", 0)
                print(f"  LLM token 用量: {llm_tokens}")

            if task_status == "completed":
                total_extracted = (
                    sum(len(v) if isinstance(v, list) else v for v in memories_extracted.values())
                    if memories_extracted
                    else 0
                )
                if total_extracted > 0:
                    print(f"✓ 记忆提取成功，共提取 {total_extracted} 条记忆")
                    result["steps"]["memory_extraction"] = "success"
                else:
                    print("⚠ 记忆提取完成但结果为空 (VLM 可能返回了无效 JSON)")
                    result["steps"]["memory_extraction"] = "empty"
            elif task_status == "failed":
                error_msg = task_result.get("error", "unknown error")
                print(f"✗ 记忆提取失败: {error_msg}")
                result["steps"]["memory_extraction"] = "failed"
            else:
                print("✗ 记忆提取超时")
                result["steps"]["memory_extraction"] = "timeout"

            result["task_result"] = task_result

            # 步骤 5: 验证记忆文件变化（硬性断言）
            print("\n[步骤 5/5] 验证记忆文件变化")
            memory_files_result = self.check_memory_files(
                scenario["memory_type"], before_memory_files
            )
            result["memory_files"] = memory_files_result

            if memory_files_result["found"]:
                new_count = len(memory_files_result["new_files"])
                mod_count = len(memory_files_result["modified_files"])
                print(f"✓ 记忆文件有变化，新增 {new_count} 个，更新 {mod_count} 个")
                result["steps"]["memory_files"] = "success"
            else:
                print("✗ 记忆文件验证失败（全目录无新增或修改的文件）")
                result["steps"]["memory_files"] = "failed"

            # 综合判断：记忆提取成功 + 文件有变化 = 通过
            extraction_ok = result["steps"].get("memory_extraction") == "success"
            files_ok = result["steps"].get("memory_files") == "success"

            if extraction_ok and files_ok:
                result["status"] = "passed"
                return True, result
            else:
                reasons = []
                if not extraction_ok:
                    reasons.append("记忆提取失败或为空")
                if not files_ok:
                    reasons.append("无新增或修改的记忆文件")
                result["status"] = "failed"
                result["error"] = f"{scenario['memory_type']}: {', '.join(reasons)}"
                return False, result

        except Exception as e:
            print(f"\n✗ 场景测试执行失败: {e}")
            result["status"] = "error"
            result["error"] = str(e)
            return False, result

    def run_full_test_suite(self) -> Dict[str, Any]:
        """运行完整的测试套件"""
        print("\n" + "=" * 60)
        print("Memory V2 全面端到端测试套件")
        print("  对话方式: OpenClaw agent")
        print("  Commit 方式: OV API")
        print("=" * 60)
        print(f"\n测试场景数量: {len(self.test_scenarios)}")
        print(f"记忆类型覆盖: {', '.join([s['memory_type'] for s in self.test_scenarios])}")
        print(f"数据目录: {self.viking_data_dir}")
        print(f"OV Server: {SERVER_URL}")

        results = {
            "total_scenarios": len(self.test_scenarios),
            "scenarios": [],
            "summary": {"passed": 0, "failed": 0, "error": 0},
        }

        for scenario in self.test_scenarios:
            passed, result = self.test_single_scenario(scenario)
            results["scenarios"].append(result)

            if result["status"] == "passed":
                results["summary"]["passed"] += 1
            elif result["status"] == "failed":
                results["summary"]["failed"] += 1
            else:
                results["summary"]["error"] += 1

        print("\n" + "=" * 60)
        print("测试总结")
        print("=" * 60)
        print(f"总场景数: {results['total_scenarios']}")
        print(f"通过: {results['summary']['passed']}")
        print(f"失败: {results['summary']['failed']}")
        print(f"错误: {results['summary']['error']}")

        pass_rate = results["summary"]["passed"] / results["total_scenarios"]
        print(f"\n通过率: {pass_rate * 100:.1f}%")

        if pass_rate >= 0.7:
            print("\n✓ Memory V2 测试套件通过！")
        else:
            print("\n✗ Memory V2 测试套件未通过")

        return results


SCENARIO_MAP = {
    "preferences": {
        "name": "preferences",
        "description": "测试偏好设置记忆",
        "test_message": "我喜欢用Python编程，偏好使用VS Code编辑器，喜欢喝咖啡，特别是美式咖啡",
        "memory_type": "preferences",
    },
    "entities": {
        "name": "entities",
        "description": "测试实体信息记忆",
        "test_message": "我叫李明，今年28岁，是一名软件工程师，在字节跳动工作，住在北京海淀区",
        "memory_type": "entities",
    },
    "events": {
        "name": "events",
        "description": "测试事件记录记忆",
        "test_message": "昨天我参加了公司的技术分享会，主题是微服务架构，会议持续了2小时，有50多人参加",
        "memory_type": "events",
    },
    "profile": {
        "name": "profile",
        "description": "测试用户画像记忆",
        "test_message": "我是一名技术负责人，有10年开发经验，专注于后端架构设计，喜欢用Python和Go语言",
        "memory_type": "profile",
    },
    "skills": {
        "name": "skills",
        "description": "测试技能记忆",
        "test_message": "我擅长使用Docker进行容器化部署，熟练掌握Kubernetes集群管理，有丰富的CI/CD流水线搭建经验",
        "memory_type": "skills",
    },
    "tools": {
        "name": "tools",
        "description": "测试工具记忆",
        "test_message": "我经常使用Git进行版本控制，用Jenkins做持续集成，用Prometheus和Grafana监控服务",
        "memory_type": "tools",
    },
}


def _run_single_memory_test(scenario_key: str):
    tester = MemoryV2TestSuite()
    scenario = SCENARIO_MAP[scenario_key]
    passed, result = tester.test_single_scenario(scenario)
    assert passed, f"{scenario['name']} 测试失败: {result.get('error', '未知错误')}"


def test_memory_v2_preferences():
    _run_single_memory_test("preferences")


def test_memory_v2_entities():
    _run_single_memory_test("entities")


def test_memory_v2_events():
    _run_single_memory_test("events")


def test_memory_v2_profile():
    _run_single_memory_test("profile")


def test_memory_v2_skills():
    _run_single_memory_test("skills")


def test_memory_v2_tools():
    _run_single_memory_test("tools")


if __name__ == "__main__":
    """直接运行测试"""
    tester = MemoryV2TestSuite()
    results = tester.run_full_test_suite()

    print("\n" + "=" * 60)
    print("详细测试报告")
    print("=" * 60)

    for scenario in results["scenarios"]:
        status_icon = "✓" if scenario["status"] == "passed" else "✗"
        print(f"\n{status_icon} {scenario['scenario']}: {scenario['status']}")
        steps = scenario.get("steps", {})
        for step_name, step_status in steps.items():
            step_icon = (
                "✓" if step_status in ("success",) else "⚠" if step_status == "empty" else "✗"
            )
            print(f"  {step_icon} {step_name}: {step_status}")
        if scenario.get("error"):
            print(f"  错误: {scenario['error']}")

    exit(0 if results["summary"]["passed"] >= results["total_scenarios"] * 0.7 else 1)
