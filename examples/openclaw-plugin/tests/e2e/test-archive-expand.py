#!/usr/bin/env python3
"""
ov_archive_expand 归档展开端到端测试 — 用户: 小杰（后端开发新人）

================================================================================
一、用例设计思路
================================================================================

核心验证点:
  当对话累积到一定量后，早期内容会被压缩归档（archive），归档摘要只保留概要
  信息，精确的参数值（IP、端口、命令、hash 等）会在压缩中丢失。当用户追问这些
  精确细节时，LLM 需要通过调用 ov_archive_expand 工具展开归档，从原始对话中
  恢复精确数据，才能给出正确回答。

  本用例通过以下策略验证该能力:
    1. 注入大量包含精确参数的对话（pod 名、kubectl 命令、PR 编号、行号、
       benchmark 结果、commit hash、incident report 编号等）
    2. 4 批对话 × 8 轮 = 32 轮对话，迫使系统产生多个归档
    3. 追问精确细节，验证 LLM 是否调用 ov_archive_expand 并返回精确数据
    4. 对比：概要级问题无需展开即可回答（验证展开的必要性）

对话数据设计:
  - CHAT_BATCH_1 (8轮): 项目技术细节 — Kafka config、JWT 参数、ClickHouse 表名、
    部署脚本、告警规则、代码规范、bug 修复等
  - CHAT_BATCH_2 (8轮): 线上排障过程 — 具体 pod 名、kubectl 命令、IP 地址、
    tcpdump 命令、配置参数、curl 测试结果、incident report
  - CHAT_BATCH_3 (8轮): 代码评审讨论 — PR 编号、具体文件行号、review comment、
    benchmark ns/op、覆盖率百分比、commit hash、hotfix PR
  - CHAT_BATCH_4 (8轮): 架构设计讨论 — Redis 配置、gRPC proto、缓存 key 格式、
    HPA 参数、Confluence 页面 ID

  这些数据包含大量"精确值"（数字、命令、ID），是摘要压缩时最容易丢失的信息，
  也是 ov_archive_expand 最核心的价值场景。

验证查询设计:
  - EXPAND_QUESTIONS (4题): 追问精确参数 — 预期触发 ov_archive_expand
    每题设置 expected_keywords 和 target_archive，用关键词命中率 >= 50% 判定
  - NO_EXPAND_QUESTIONS (1题): 概要级问题 — 预期从摘要即可回答，无需展开

断言策略:
  - 关键词命中率 >= 50% 即判定通过（允许 LLM 回复的表述差异）
  - 通过 openclaw.log 中的 "ov_archive_expand invoked/expanded" 日志验证
    工具是否真正被调用

================================================================================
二、测试流程
================================================================================

  Phase 1:   第一段对话 (8 轮) — 项目技术细节 → afterTurn + auto-commit
  Phase 2a:  第二段对话 (8 轮) — 线上排障过程 → afterTurn + auto-commit
  Phase 2b:  第三段对话 (8 轮) — 代码评审讨论 → afterTurn + auto-commit
  Phase 2c:  第四段对话 (8 轮) — 架构设计讨论 → afterTurn + auto-commit
  Phase 3:   验证 Archive Index — 检查 commit_count、记忆数、归档数
  Phase 4:   追问精确细节 (4 问) — 触发 ov_archive_expand，验证关键词命中
  Phase 5:   概要级问题 (1 问) — 验证无需展开即可回答

  可选: 在 Phase 4 前通过 --gateway-restart-cmd 重启 Gateway 清除工作记忆，
  迫使 LLM 完全依赖归档获取信息（更严格的验证）。

================================================================================
三、环境前提
================================================================================

  1. OpenViking 服务已启动（提供归档存储和展开能力）
  2. OpenClaw Gateway 已启动并配置了 OpenViking 插件
  3. LLM 后端可达（Gateway 需要调用 LLM 生成回复和触发工具调用）
  4. 有效的 Gateway auth token（通过 --token 传入或自动发现）

  服务部署参考:
    - OpenViking: openviking-server（HTTP 默认 2934，AGFS 默认 2833）
    - Gateway: openclaw gateway（HTTP 默认 19789）

================================================================================
四、使用方法
================================================================================

  安装依赖:
    pip install requests rich

  完整测试 (推荐):
    python test-archive-expand.py --phase all \\
        --gateway http://127.0.0.1:19789 \\
        --openviking http://127.0.0.1:2934 \\
        --token <your_gateway_token>

  分阶段执行:
    python test-archive-expand.py --phase chat1       # 仅第一批对话
    python test-archive-expand.py --phase chat2       # 仅第二批对话
    python test-archive-expand.py --phase verify-index # 仅验证归档索引
    python test-archive-expand.py --phase expand       # 仅追问精确细节
    python test-archive-expand.py --phase no-expand    # 仅概要级问题

  其他选项:
    --user-id <id>      固定用户 ID（默认随机生成）
    --delay <seconds>    轮次间等待秒数（默认 3s）
    --verbose / -v       详细输出（显示完整 JSON 响应）
    --gateway-restart-cmd <cmd>  Phase 4 前重启 Gateway 的命令
    --log-path <path>    Gateway 日志路径，测试后自动扫描 ov_archive_expand 调用记录

  注意:
    - 完整测试约需 10-15 分钟（32 轮对话 + 验证 + 追问）
    - 首次运行前建议清理 OV 数据和 session 数据，避免干扰

================================================================================
五、验证工具调用（日志检查）
================================================================================

  本脚本通过关键词命中率间接验证 ov_archive_expand 是否生效。如需直接确认
  工具是否被调用，可通过以下方式检查 Gateway 日志:

  方式 1 — 自动检查（推荐）:
    传入 --log-path 参数，脚本结束后自动扫描并打印工具调用记录:

    python test-archive-expand.py --phase all \\
        --log-path config/.openclaw/logs/openclaw.log

  方式 2 — 手动检查:
    # Linux / macOS
    grep "ov_archive_expand" config/.openclaw/logs/openclaw.log

    # Windows PowerShell
    Select-String -Path "config\\.openclaw\\logs\\openclaw.log" \\
        -Pattern "ov_archive_expand"

  预期日志（每次展开会产生一对 invoked + expanded 日志）:

    openviking: ov_archive_expand invoked (archiveId=archive_001, sessionId=...)
    openviking: ov_archive_expand expanded archive_001, messages=17, chars=82675, ...

  如果 Phase 4 通过但日志中没有 ov_archive_expand 记录，说明 LLM 可能是
  从工作记忆（而非归档展开）中获取的信息。此时可通过 --gateway-restart-cmd
  在 Phase 4 前重启 Gateway 清除工作记忆，强制走归档展开路径。

================================================================================
六、预期结果
================================================================================

  15/15 断言全部通过:
    - Phase 1~2c: 32 轮对话全部成功
    - Phase 3: commit_count >= 3, 归档数 >= 3, 记忆提取数 > 0
    - Phase 4: 4 个追问全部命中关键词 (>= 50%)
    - Phase 5: 概要回答正确
"""

import argparse
import io
import json
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Any

import requests
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 常量 ───────────────────────────────────────────────────────────────────

USER_ID = f"xiaojie-{uuid.uuid4().hex[:8]}"
DISPLAY_NAME = "小杰"
DEFAULT_GATEWAY = "http://127.0.0.1:19789"
DEFAULT_OPENVIKING = "http://127.0.0.1:2934"
AGENT_ID = "main"

console = Console(force_terminal=True)

# ── 测试结果收集 ──────────────────────────────────────────────────────────

assertions: list[dict] = []


def check(label: str, condition: bool, detail: str = ""):
    assertions.append({"label": label, "ok": condition, "detail": detail})
    icon = "[green]PASS[/green]" if condition else "[red]FAIL[/red]"
    msg = f"  {icon} {label}"
    if detail:
        msg += f"  [dim]({detail})[/dim]"
    console.print(msg)


# ── 第一批对话: 项目技术细节 (8 轮) ──────────────────────────────────────

CHAT_BATCH_1 = [
    "嗨！我叫小杰，刚入职三个月，在做一个用户画像系统。后端用 Go，框架是 Gin，数据库用 ClickHouse。我想跟你聊聊项目的技术细节，你帮我记一下。",

    "我们的 API 认证用的是自己写的 JWT 中间件，token 过期时间设的 7200 秒，刷新 token 有效期 30 天，密钥存在环境变量 AUTH_JWT_SECRET 里。",

    "数据采集这块，我写了个 Kafka consumer，group id 是 user-profile-sync-v2，消费的 topic 是 user_behavior_events，每批最多拉 500 条消息。",

    "画像数据的 ClickHouse 表叫 user_profiles_v3，主键是 (user_id, event_date)，用了 MergeTree 引擎，TTL 设的 180 天。",

    "部署脚本在 deploy/scripts/rollout.sh，里面有个关键的金丝雀发布逻辑，先把 10% 流量切到新版本，观察 5 分钟没报警再全量。",

    "我们的 Prometheus 告警规则在 monitoring/alerts/backend.yml，有一条关键的：当 P99 延迟超过 500ms 持续 3 分钟就会触发 page 告警。",

    "代码规范方面，Go 项目用 golangci-lint，配置文件在 .golangci.yml，禁用了 gocyclo，开启了 govet、errcheck、staticcheck。",

    "上周修了个严重 bug：当 ClickHouse 连接超时时，consumer 没有正确回退 offset，导致消息丢失。我写了个 RetryableConsumer wrapper 来修复，重试间隔是指数退避，基础间隔 200ms，最大重试 5 次。",
]

# ── 第二批对话: 某次线上排障过程 (8 轮) ──────────────────────────────────
# 嵌入大量过程性细节（具体命令、错误信息、临时端口），这些不太会被摘要保留

CHAT_BATCH_2 = [
    "紧急情况！线上推荐接口大面积超时，错误日志里出现了一条：failed to connect to reco-model-svc:8091: dial tcp 10.0.3.17:8091: i/o timeout。我先帮你记录下排障过程。",

    "我先跑了 kubectl get pods -n reco-prod，发现 reco-model-svc-7b9f4d6c8-x2k9p 这个 pod 的 RESTARTS 是 47 次，状态是 CrashLoopBackOff。kubectl logs 看到 OOM Killed，内存限制是 512Mi 但模型加载需要 800Mi。",

    "临时解决方案：kubectl edit deployment reco-model-svc -n reco-prod，把 resources.limits.memory 从 512Mi 改成 1Gi，然后 kubectl rollout restart deployment reco-model-svc -n reco-prod。等了 3 分钟 pod 恢复正常。",

    "但还有个隐患：我用 tcpdump -i eth0 port 8091 -w /tmp/reco-debug-20260315.pcap 抓了 5 分钟的包，发现有个上游服务 gateway-proxy (IP 10.0.2.33) 的连接没有正确关闭，导致连接泄漏。",

    "连接泄漏的根因：gateway-proxy 用了一个自定义的 HTTP client，pool_maxsize 设成了 200，但 idle_timeout 是 0（永不超时）。我在 gateway-proxy 的 config/http-pool.yaml 里改成了 idle_timeout: 30s，pool_maxsize: 50。",

    "修完之后跑了个回归测试：curl -w '@curl-format.txt' -o /dev/null -s 'http://10.0.3.17:8091/predict?user_id=test_user_42&features=age,gender,region' 返回 time_total: 0.023s，比之前的 2.1s 快了 100 倍。",

    "事后我写了个 incident report，编号是 INC-2026-0315-RECO-OOM，根因分类是 Resource Misconfiguration，影响时长 47 分钟，影响用户数约 12000。",

    "老王看完报告说，以后所有服务的 memory limit 至少设置为实际用量的 1.5 倍，并且要在 Grafana 上加一个 container_memory_working_set_bytes / container_spec_memory_limit_bytes > 0.8 的告警。",
]

# ── 第三批对话: 代码评审中的具体讨论 (8 轮) ────────────────────────────
# 嵌入代码审查中的具体 review comment 和代码片段

CHAT_BATCH_3 = [
    "今天代码评审了我的推荐接口 PR，PR 编号是 #1847。老王给了 3 个重要 comment，我一个个跟你说。",

    "第一个 comment 在 internal/handler/recommend.go 的第 73 行：老王说我的错误处理不对，原来写的是 if err != nil { return nil, err }，但应该包装一下上下文：return nil, fmt.Errorf('recommend handler: fetch features for user %s: %w', userID, err)。",

    "第二个 comment 在 internal/cache/feature_cache.go 第 142 行：我用了 sync.Map 来缓存用户特征，但老王建议改用分段锁 map，因为 sync.Map 在写多读少的场景下性能不好。他推荐用 github.com/orcaman/concurrent-map/v2 这个库。",

    "第三个 comment 是关于测试覆盖率的：当前 recommend 包的覆盖率只有 38%，老王要求至少到 70%。他特别指出 internal/handler/recommend_test.go 缺少对 context.Canceled 和 context.DeadlineExceeded 的边界测试。",

    "我按老王的建议改了代码。feature_cache.go 的改动最大，从 sync.Map 迁移到 cmap.ConcurrentMap[string, *UserFeatures]。benchmark 跑下来：BenchmarkFeatureCacheGet-8 从 834 ns/op 降到了 412 ns/op，快了差不多一倍。",

    "测试也补了，加了 TestRecommendHandler_ContextCanceled 和 TestRecommendHandler_DeadlineExceeded 两个用例。覆盖率从 38% 提升到了 74%。go test -cover ./internal/handler/ 输出：coverage: 74.2% of statements。",

    "PR 最终在周三下午 3:42 合并，commit hash 是 a3f7b2d。合并前跑了 CI，全部 green：lint 42s, test 1m18s, build 2m03s。",

    "对了，合并后我发现有个小问题：feature_cache.go 里有一行 import 多余了，_ 'net/http/pprof' 是调试时加的忘了删。我又开了个 hotfix PR #1852 修掉了。",
]

# ── 第四批对话: 架构设计讨论 (8 轮) ─────────────────────────────────────

CHAT_BATCH_4 = [
    "最近团队在讨论要不要把推荐服务拆成微服务。我画了一个架构图，核心是 3 个服务：feature-store (负责用户特征存储), model-server (负责模型推理), ranking-api (负责排序和过滤)。",

    "feature-store 的设计：用 Redis Cluster 做热数据缓存，冷数据存 ClickHouse。Redis 集群是 3 主 3 从，每个节点 maxmemory 8GB，eviction 策略用 allkeys-lru。",

    "model-server 计划用 gRPC 通信，proto 文件在 api/proto/model_service.proto。核心 RPC 是 Predict(PredictRequest) returns (PredictResponse)，PredictRequest 里有 user_id (string), features (map<string, float>), model_version (string, 默认 'v3.2.1')。",

    "ranking-api 是面向外部的 REST 接口。我设计了一个两层缓存：L1 是本地 LRU cache (github.com/hashicorp/golang-lru/v2, 容量 10000), L2 是 Redis。缓存 key 的格式是 reco:{user_id}:{model_version}:{timestamp_bucket}，timestamp_bucket 每 5 分钟一个。",

    "团队讨论的争议点：老王认为 feature-store 和 model-server 可以合并，因为两者耦合度高。但我觉得拆开更好，因为 feature-store 的扩展需求（加新特征）和 model-server 的扩展需求（换模型）是独立的。",

    "最终架构评审的结论：先按 3 服务拆分，但 feature-store 和 model-server 共享一个 K8s namespace (reco-services)。服务间通信走 Istio service mesh，mTLS 加密。",

    "部署策略：feature-store 3 副本（HPA min=3, max=10, CPU 阈值 70%），model-server 2 副本（HPA min=2, max=6, CPU 阈值 60%），ranking-api 4 副本（HPA min=4, max=20, CPU 阈值 65%）。",

    "对了，架构评审文档存在 Confluence 上，页面 ID 是 ARCH-2026-RECO-MS，最后更新时间是 3 月 20 号。评审参与人：我、老王、测试小李、运维老赵。",
]

# ── 追问精确细节 (触发 archive expand) ──────────────────────────────────
# 问的都是过程性细节：具体命令、IP 地址、错误信息、commit hash 等
# 这些内容在摘要中通常会被压缩掉

EXPAND_QUESTIONS = [
    {
        "question": "之前那次线上推荐接口故障，出问题的 pod 名字是什么？kubectl logs 看到的错误是什么？最终怎么临时修的？请给我精确的命令。",
        "expected_keywords": ["7b9f4d6c8-x2k9p", "OOM", "512Mi", "1Gi"],
        "target_archive": "archive_002",
        "description": "追问排障过程中的 pod 名和命令",
    },
    {
        "question": "我之前代码评审那个 PR 编号是多少？老王在哪个文件的第几行给了 comment？关于错误处理他具体建议怎么改？",
        "expected_keywords": ["1847", "recommend.go", "73"],
        "target_archive": "archive_003",
        "description": "追问代码评审的精确 PR 和行号",
    },
    {
        "question": "feature_cache.go 迁移后的 benchmark 结果是多少 ns/op？测试覆盖率从多少提升到了多少？PR 合并的 commit hash 是什么？",
        "expected_keywords": ["412", "38%", "74", "a3f7b2d"],
        "target_archive": "archive_003",
        "description": "追问 benchmark 和覆盖率精确数据",
    },
    {
        "question": "那次故障的 incident report 编号是什么？影响了多少用户？连接泄漏的根因是什么配置导致的？",
        "expected_keywords": ["INC-2026-0315", "12000", "idle_timeout"],
        "target_archive": "archive_002",
        "description": "追问故障报告和根因",
    },
]

# ── 不需要展开的问题 ────────────────────────────────────────────────────

NO_EXPAND_QUESTIONS = [
    {
        "question": "我做什么项目的？用什么技术栈？请简洁回答。",
        "expected_keywords": ["用户画像", "Go"],
        "description": "概要信息，不需要展开归档",
    },
]


# ── Token 自动发现 ────────────────────────────────────────────────────────

_gateway_token: str = ""


def discover_gateway_token() -> str:
    import pathlib

    candidates = [
        pathlib.Path.cwd() / "config" / ".openclaw" / "openclaw.json",
        pathlib.Path.home() / ".openclaw" / "openclaw.json",
    ]
    state_dir = os.environ.get("OPENCLAW_STATE_DIR")
    if state_dir:
        candidates.insert(0, pathlib.Path(state_dir) / "openclaw.json")

    for p in candidates:
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
            if token:
                return token
        except Exception:
            continue
    return ""


def set_gateway_token(token: str):
    global _gateway_token
    _gateway_token = token


# ── Gateway / OpenViking API ─────────────────────────────────────────────


def send_message(gateway_url: str, message: str, user_id: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if _gateway_token:
        headers["Authorization"] = f"Bearer {_gateway_token}"
    resp = requests.post(
        f"{gateway_url}/v1/responses",
        headers=headers,
        json={"model": "openclaw", "input": message, "user": user_id},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()


def extract_reply_text(data: dict) -> str:
    for item in data.get("output", []):
        if item.get("type") == "message" and item.get("role") == "assistant":
            for part in item.get("content", []):
                if part.get("type") in ("text", "output_text"):
                    return part.get("text", "")
    return "(无回复)"


class OVInspector:
    def __init__(self, base_url: str, agent_id: str = AGENT_ID):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id

    def _headers(self) -> dict:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.agent_id:
            h["X-OpenViking-Agent"] = self.agent_id
        return h

    def _get(self, path: str, timeout: int = 10):
        try:
            resp = requests.get(
                f"{self.base_url}{path}", headers=self._headers(), timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", data)
            return None
        except Exception as e:
            console.print(f"[dim]GET {path} failed: {e}[/dim]")
            return None

    def _post(self, path: str, body: dict | None = None, timeout: int = 30):
        try:
            resp = requests.post(
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=body or {},
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", data)
            return None
        except Exception as e:
            console.print(f"[dim]POST {path} failed: {e}[/dim]")
            return None

    def health_check(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/health", timeout=5).status_code == 200
        except Exception:
            return False

    def list_sessions(self) -> list:
        result = self._get("/api/v1/sessions")
        return result if isinstance(result, list) else []

    def get_session(self, session_id: str):
        return self._get(f"/api/v1/sessions/{session_id}")

    def get_session_context(self, session_id: str, token_budget: int = 128000):
        return self._get(
            f"/api/v1/sessions/{session_id}/context?token_budget={token_budget}",
        )

    def commit_session(self, session_id: str, wait: bool = True):
        result = self._post(f"/api/v1/sessions/{session_id}/commit", timeout=120)
        if not result:
            return None
        if wait and result.get("task_id"):
            task_id = result["task_id"]
            deadline = time.time() + 120
            while time.time() < deadline:
                time.sleep(0.5)
                task = self._get(f"/api/v1/tasks/{task_id}")
                if not task:
                    continue
                if task.get("status") == "completed":
                    result["status"] = "completed"
                    return result
                if task.get("status") == "failed":
                    result["status"] = "failed"
                    return result
        return result

    def find_latest_session(self) -> str | None:
        sessions = self.list_sessions()
        real = [
            s for s in sessions
            if isinstance(s, dict)
            and not s.get("session_id", "").startswith("memory-store-")
        ]
        if not real:
            return None
        best_id, best_time = None, ""
        for s in real:
            sid = s.get("session_id", "")
            if not sid:
                continue
            detail = self.get_session(sid)
            if not detail:
                continue
            updated = detail.get("updated_at", "")
            if updated > best_time:
                best_time = updated
                best_id = sid
        return best_id or (real[-1].get("session_id") if real else None)


# ── 渲染函数 ─────────────────────────────────────────────────────────────


def render_reply(text: str, title: str = "回复"):
    lines = text.split("\n")
    if len(lines) > 25:
        text = "\n".join(lines[:25]) + f"\n\n... (共 {len(lines)} 行，已截断)"
    console.print(Panel(Markdown(text), title=f"[green]{title}[/green]", border_style="green"))


def render_json(data: Any, title: str = "JSON"):
    console.print(
        Panel(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:2000], title=title),
    )


# ── Phase 1: 第一批对话 ──────────────────────────────────────────────────


def run_phase_chat(
    gateway_url: str, user_id: str, messages: list[str],
    batch_label: str, delay: float, verbose: bool,
) -> tuple[int, int]:
    console.print()
    console.rule(
        f"[bold]{batch_label}: {DISPLAY_NAME} 对话 ({len(messages)} 轮)[/bold]",
    )
    console.print(f"[yellow]用户ID:[/yellow] {user_id}")
    console.print()

    total = len(messages)
    ok = fail = 0

    for i, msg in enumerate(messages, 1):
        console.rule(f"[dim]Turn {i}/{total}[/dim]", style="dim")
        preview = msg[:150] + ("..." if len(msg) > 150 else "")
        console.print(
            Panel(preview, title=f"[bold cyan]{DISPLAY_NAME} [{i}/{total}][/bold cyan]", border_style="cyan"),
        )
        try:
            data = send_message(gateway_url, msg, user_id)
            reply = extract_reply_text(data)
            render_reply(reply[:400] + ("..." if len(reply) > 400 else ""))
            ok += 1
        except Exception as e:
            console.print(f"[red][ERROR][/red] {e}")
            fail += 1

        if i < total:
            time.sleep(delay)

    console.print()
    console.print(f"[yellow]对话完成:[/yellow] {ok} 成功, {fail} 失败")

    wait = max(delay * 2, 8)
    console.print(f"[yellow]等待 {wait:.0f}s 让 afterTurn + auto-commit 处理...[/yellow]")
    time.sleep(wait)

    return ok, fail


# ── Phase 3: 验证 Archive Index 存在 ────────────────────────────────────


def run_phase_verify_index(openviking_url: str, verbose: bool) -> str:
    console.print()
    console.rule("[bold]Phase 3: 验证 Archive Index 存在[/bold]")
    console.print()

    inspector = OVInspector(openviking_url)

    healthy = inspector.health_check()
    check("OpenViking 服务可达", healthy)
    if not healthy:
        return ""

    session_id = inspector.find_latest_session()
    check("OV session 存在", session_id is not None, f"session_id={session_id}")
    if not session_id:
        return ""

    session_info = inspector.get_session(session_id)
    if session_info:
        commit_count = session_info.get("commit_count", 0)
        check(
            "commit_count >= 3 (至少 3 个 archive)",
            commit_count >= 3,
            f"commit_count={commit_count}",
        )

        memories = session_info.get("memories_extracted", {})
        total_mem = sum(memories.values()) if isinstance(memories, dict) else 0
        check("累计提取记忆 > 0", total_mem > 0, f"total={total_mem}")

        if verbose:
            render_json(session_info, "Session 详情")

    ctx = inspector.get_session_context(session_id)
    if ctx:
        overview = ctx.get("latest_archive_overview", "")
        messages = ctx.get("messages", [])
        stats = ctx.get("stats", {})

        check(
            "context 返回数据",
            bool(overview) or len(messages) > 0,
            f"overview_len={len(overview)}, messages={len(messages)}",
        )

        total_archives = stats.get("totalArchives", 0)
        check(
            "归档数 >= 3",
            total_archives >= 3,
            f"totalArchives={total_archives}",
        )

        if verbose and overview:
            console.print(f"  [dim]overview 前 300 字: {overview[:300]}...[/dim]")
    else:
        check("context 可调用", False)

    return session_id or ""


# ── Phase 4: 追问精确细节 — 触发 ov_archive_expand ──────────────────────


def run_phase_expand(
    gateway_url: str, user_id: str, delay: float, verbose: bool,
) -> list:
    console.print()
    console.rule(
        f"[bold]Phase 4: 追问精确细节 — 触发 ov_archive_expand ({len(EXPAND_QUESTIONS)} 轮)[/bold]",
    )
    console.print()
    console.print("[dim]验证点:[/dim]")
    console.print("[dim]- 追问归档中的精确参数值[/dim]")
    console.print("[dim]- LLM 应通过 ov_archive_expand 展开归档[/dim]")
    console.print("[dim]- 回复包含原始对话中的精确数据（非泛化摘要）[/dim]")
    console.print()

    results = []
    total = len(EXPAND_QUESTIONS)

    for i, item in enumerate(EXPAND_QUESTIONS, 1):
        q = item["question"]
        keywords = item["expected_keywords"]
        desc = item["description"]

        console.rule(f"[dim]Expand Q{i}/{total}: {desc}[/dim]", style="dim")
        console.print(
            Panel(
                f"{q}\n\n[dim]期望关键词: {', '.join(keywords)}[/dim]\n"
                f"[dim]目标归档: {item['target_archive']}[/dim]",
                title=f"[bold cyan]Expand Q{i}[/bold cyan]",
                border_style="cyan",
            ),
        )

        try:
            data = send_message(gateway_url, q, user_id)
            reply = extract_reply_text(data)
            render_reply(reply)

            reply_lower = reply.lower()
            hits = [kw for kw in keywords if kw.lower() in reply_lower]
            hit_rate = len(hits) / len(keywords) if keywords else 0
            success = hit_rate >= 0.5

            check(
                f"Expand Q{i} ({desc}): 关键词命中率 >= 50%",
                success,
                f"命中={hits}, 未命中={[k for k in keywords if k not in hits]}, rate={hit_rate:.0%}",
            )

            if verbose:
                console.print(f"  [dim]完整输出: {json.dumps(data.get('output', []), ensure_ascii=False)[:500]}[/dim]")

            results.append({
                "question": q, "hits": hits, "hit_rate": hit_rate,
                "success": success, "description": desc,
            })
        except Exception as e:
            check(f"Expand Q{i}: 发送成功", False, str(e))
            results.append({
                "question": q, "hits": [], "hit_rate": 0,
                "success": False, "description": desc,
            })

        if i < total:
            time.sleep(delay)

    return results


# ── Phase 5: 不需要展开的问题 ───────────────────────────────────────────


def run_phase_no_expand(
    gateway_url: str, user_id: str, delay: float, verbose: bool,
) -> list:
    console.print()
    console.rule(
        f"[bold]Phase 5: 不需要展开的问题 ({len(NO_EXPAND_QUESTIONS)} 轮)[/bold]",
    )
    console.print("[dim]验证: 概要级问题从摘要即可回答，无需展开[/dim]")
    console.print()

    results = []
    total = len(NO_EXPAND_QUESTIONS)

    for i, item in enumerate(NO_EXPAND_QUESTIONS, 1):
        q = item["question"]
        keywords = item["expected_keywords"]

        console.rule(f"[dim]NoExpand Q{i}/{total}[/dim]", style="dim")
        console.print(
            Panel(
                f"{q}\n\n[dim]期望关键词: {', '.join(keywords)}[/dim]",
                title=f"[bold cyan]NoExpand Q{i}[/bold cyan]",
                border_style="cyan",
            ),
        )

        try:
            data = send_message(gateway_url, q, user_id)
            reply = extract_reply_text(data)
            render_reply(reply)

            reply_lower = reply.lower()
            hits = [kw for kw in keywords if kw.lower() in reply_lower]
            hit_rate = len(hits) / len(keywords) if keywords else 0

            check(
                f"NoExpand Q{i}: 概要回答正确 (命中率 >= 50%)",
                hit_rate >= 0.5,
                f"命中={hits}, rate={hit_rate:.0%}",
            )
            results.append({"question": q, "hits": hits, "hit_rate": hit_rate, "success": hit_rate >= 0.5})
        except Exception as e:
            check(f"NoExpand Q{i}: 发送成功", False, str(e))
            results.append({"question": q, "hits": [], "hit_rate": 0, "success": False})

        if i < total:
            time.sleep(delay)

    return results


# ── 完整测试 ──────────────────────────────────────────────────────────────


def run_full_test(
    gateway_url: str, openviking_url: str, user_id: str, delay: float, verbose: bool,
    gateway_restart_cmd: str = "",
):
    console.print()
    console.print(
        Panel.fit(
            f"[bold]ov_archive_expand 归档展开测试 — {DISPLAY_NAME}[/bold]\n\n"
            f"Gateway: {gateway_url}\n"
            f"OpenViking: {openviking_url}\n"
            f"User ID: {user_id}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            title="测试信息",
        ),
    )

    # Phase 1: 第一批对话 — 项目技术细节
    ok1, fail1 = run_phase_chat(
        gateway_url, user_id, CHAT_BATCH_1,
        "Phase 1: 第一段对话 — 项目技术细节", delay, verbose,
    )
    check(f"Phase 1: {ok1}/{len(CHAT_BATCH_1)} 轮成功", fail1 == 0, f"ok={ok1}, fail={fail1}")

    # Phase 2a: 第二批对话 — 排障过程
    ok2, fail2 = run_phase_chat(
        gateway_url, user_id, CHAT_BATCH_2,
        "Phase 2a: 第二段对话 — 线上排障过程", delay, verbose,
    )
    check(f"Phase 2a: {ok2}/{len(CHAT_BATCH_2)} 轮成功", fail2 == 0, f"ok={ok2}, fail={fail2}")

    # Phase 2b: 第三批对话 — 代码评审讨论
    ok3, fail3 = run_phase_chat(
        gateway_url, user_id, CHAT_BATCH_3,
        "Phase 2b: 第三段对话 — 代码评审讨论", delay, verbose,
    )
    check(f"Phase 2b: {ok3}/{len(CHAT_BATCH_3)} 轮成功", fail3 == 0, f"ok={ok3}, fail={fail3}")

    # Phase 2c: 第四批对话 — 架构设计讨论
    ok4, fail4 = run_phase_chat(
        gateway_url, user_id, CHAT_BATCH_4,
        "Phase 2c: 第四段对话 — 架构设计讨论", delay, verbose,
    )
    check(f"Phase 2c: {ok4}/{len(CHAT_BATCH_4)} 轮成功", fail4 == 0, f"ok={ok4}, fail={fail4}")

    # Phase 3: 验证 Archive Index
    session_id = run_phase_verify_index(openviking_url, verbose)

    # Gateway 重启 — 清除工作记忆，迫使 LLM 从归档获取信息
    if gateway_restart_cmd:
        console.print()
        console.rule("[bold yellow]重启 Gateway — 清除工作记忆[/bold yellow]")
        console.print("[yellow]重启前等待 10s 让后台 commit 完成...[/yellow]")
        time.sleep(10)
        console.print(f"[yellow]执行: {gateway_restart_cmd}[/yellow]")
        import subprocess
        try:
            result = subprocess.run(
                gateway_restart_cmd, shell=True, capture_output=True, text=True, timeout=60,
            )
            console.print(f"[yellow]Gateway 重启完成: {result.stdout.strip()}[/yellow]")
        except subprocess.TimeoutExpired:
            console.print("[yellow]Gateway 重启命令超时，检查健康状态...[/yellow]")
        # 等待 Gateway 恢复
        for attempt in range(15):
            time.sleep(2)
            try:
                r = requests.get(f"{gateway_url}/health", timeout=3)
                if r.status_code == 200:
                    console.print("[green]Gateway 健康检查通过[/green]")
                    break
            except Exception:
                pass
        else:
            console.print("[red]Gateway 重启后健康检查未通过[/red]")

    # Phase 4: 追问精确细节 — 触发 expand
    expand_results = run_phase_expand(gateway_url, user_id, delay, verbose)

    # Phase 5: 不需要展开的问题
    no_expand_results = run_phase_no_expand(gateway_url, user_id, delay, verbose)

    # ── 汇总报告 ──────────────────────────────────────────────────────────

    console.print()
    console.rule("[bold]测试报告[/bold]")

    passed = sum(1 for a in assertions if a["ok"])
    failed = sum(1 for a in assertions if not a["ok"])
    total = len(assertions)

    table = Table(title=f"断言结果: {passed}/{total} 通过")
    table.add_column("#", style="bold", width=4)
    table.add_column("状态", width=6)
    table.add_column("断言", max_width=55)
    table.add_column("详情", style="dim", max_width=55)

    for i, a in enumerate(assertions, 1):
        status = "[green]PASS[/green]" if a["ok"] else "[red]FAIL[/red]"
        table.add_row(str(i), status, a["label"][:55], (a.get("detail") or "")[:55])

    console.print(table)

    tree = Tree(f"[bold]通过: {passed}/{total}, 失败: {failed}[/bold]")
    tree.add(f"Phase 1: 项目技术细节 — {ok1}/{len(CHAT_BATCH_1)}")
    tree.add(f"Phase 2a: 线上排障 — {ok2}/{len(CHAT_BATCH_2)}")
    tree.add(f"Phase 2b: 代码评审 — {ok3}/{len(CHAT_BATCH_3)}")
    tree.add(f"Phase 2c: 架构设计 — {ok4}/{len(CHAT_BATCH_4)}")
    tree.add(f"Phase 3: Archive Index 验证")

    expand_ok = sum(1 for r in expand_results if r["success"])
    tree.add(f"Phase 4: 归档展开 — {expand_ok}/{len(expand_results)} 问题回答正确")

    no_expand_ok = sum(1 for r in no_expand_results if r["success"])
    tree.add(f"Phase 5: 无需展开 — {no_expand_ok}/{len(no_expand_results)} 问题回答正确")

    fail_list = [a for a in assertions if not a["ok"]]
    if fail_list:
        fail_branch = tree.add(f"[red]失败断言 ({len(fail_list)})[/red]")
        for a in fail_list:
            fail_branch.add(f"[red]FAIL[/red] {a['label']}")

    console.print(tree)

    if failed == 0:
        console.print("\n[green bold]全部通过! ov_archive_expand 归档展开验证成功。[/green bold]")
    else:
        console.print(f"\n[red bold]有 {failed} 个断言失败。[/red bold]")


# ── 日志扫描: 验证 ov_archive_expand 工具调用 ────────────────────────────


def scan_expand_log(log_path: str):
    """扫描 Gateway 日志，提取 ov_archive_expand 调用记录。"""
    import pathlib

    p = pathlib.Path(log_path)
    if not p.exists():
        console.print(f"\n[yellow]日志文件不存在: {log_path}[/yellow]")
        console.print("[dim]跳过工具调用日志验证[/dim]")
        return

    console.print()
    console.rule("[bold]ov_archive_expand 工具调用日志验证[/bold]")
    console.print(f"[dim]日志文件: {log_path}[/dim]")
    console.print()

    invoked_lines = []
    expanded_lines = []

    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "ov_archive_expand invoked" in line:
                    invoked_lines.append(line.strip())
                elif "ov_archive_expand expanded" in line:
                    expanded_lines.append(line.strip())
    except Exception as e:
        console.print(f"[red]读取日志失败: {e}[/red]")
        return

    if not invoked_lines and not expanded_lines:
        console.print("[red]未找到 ov_archive_expand 调用记录！[/red]")
        console.print(
            "[dim]可能原因: LLM 从工作记忆（而非归档展开）获取了信息。"
            "尝试使用 --gateway-restart-cmd 在 Phase 4 前重启 Gateway。[/dim]",
        )
        return

    log_table = Table(title="ov_archive_expand 调用记录", show_lines=True)
    log_table.add_column("#", style="bold", width=4)
    log_table.add_column("操作", width=10)
    log_table.add_column("归档 ID", style="cyan", width=14)
    log_table.add_column("详情", style="dim")

    import re
    row_idx = 0
    for line in invoked_lines:
        row_idx += 1
        m = re.search(r"archiveId=(\w+)", line)
        archive_id = m.group(1) if m else "?"
        log_table.add_row(str(row_idx), "invoked", archive_id, "调用展开")

    for line in expanded_lines:
        row_idx += 1
        m_id = re.search(r"expanded (\w+)", line)
        m_msg = re.search(r"messages=(\d+)", line)
        m_chars = re.search(r"chars=(\d+)", line)
        archive_id = m_id.group(1) if m_id else "?"
        msgs = m_msg.group(1) if m_msg else "?"
        chars = m_chars.group(1) if m_chars else "?"
        log_table.add_row(
            str(row_idx), "expanded", archive_id,
            f"恢复 {msgs} 条消息, {chars} 字符",
        )

    console.print(log_table)

    archive_counts: dict[str, int] = {}
    for line in invoked_lines:
        m = re.search(r"archiveId=(\w+)", line)
        if m:
            aid = m.group(1)
            archive_counts[aid] = archive_counts.get(aid, 0) + 1

    console.print()
    console.print(f"[green]共 {len(invoked_lines)} 次 invoked, "
                  f"{len(expanded_lines)} 次 expanded[/green]")
    for aid, cnt in sorted(archive_counts.items()):
        console.print(f"  {aid}: {cnt} 次调用")

    check(
        "日志中存在 ov_archive_expand 调用记录",
        len(invoked_lines) > 0,
        f"invoked={len(invoked_lines)}, expanded={len(expanded_lines)}",
    )


# ── 入口 ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description=f"ov_archive_expand 归档展开测试 — {DISPLAY_NAME}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY, help=f"Gateway 地址 (默认: {DEFAULT_GATEWAY})")
    parser.add_argument("--openviking", default=DEFAULT_OPENVIKING, help=f"OpenViking 地址 (默认: {DEFAULT_OPENVIKING})")
    parser.add_argument("--user-id", default=USER_ID, help="测试用户 ID (默认: 随机)")
    parser.add_argument(
        "--phase",
        choices=["all", "chat1", "chat2", "verify-index", "expand", "no-expand"],
        default="all",
        help="运行阶段 (默认: all)",
    )
    parser.add_argument("--delay", type=float, default=3.0, help="轮次间等待秒数 (默认: 3)")
    parser.add_argument("--token", default="", help="Gateway auth token (默认: 自动发现)")
    parser.add_argument("--agent-id", default=AGENT_ID, help=f"Agent ID (默认: {AGENT_ID})")
    parser.add_argument(
        "--gateway-restart-cmd", default="",
        help="Gateway 重启命令 (在 Phase 4 前执行，清除工作记忆以迫使 archive expand)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument(
        "--log-path", default="",
        help="Gateway 日志路径 (如 config/.openclaw/logs/openclaw.log)，测试后自动扫描 ov_archive_expand 调用",
    )
    args = parser.parse_args()

    gateway_url = args.gateway.rstrip("/")
    openviking_url = args.openviking.rstrip("/")
    user_id = args.user_id

    token = args.token or discover_gateway_token()
    set_gateway_token(token)

    console.print(f"[bold]ov_archive_expand 归档展开测试 — {DISPLAY_NAME}[/bold]")
    console.print(f"[yellow]Gateway:[/yellow] {gateway_url}")
    console.print(f"[yellow]OpenViking:[/yellow] {openviking_url}")
    console.print(f"[yellow]User ID:[/yellow] {user_id}")

    if args.phase == "all":
        run_full_test(
            gateway_url, openviking_url, user_id, args.delay, args.verbose,
            gateway_restart_cmd=args.gateway_restart_cmd,
        )
    elif args.phase == "chat1":
        run_phase_chat(gateway_url, user_id, CHAT_BATCH_1, "Phase 1", args.delay, args.verbose)
    elif args.phase == "chat2":
        run_phase_chat(gateway_url, user_id, CHAT_BATCH_2, "Phase 2", args.delay, args.verbose)
    elif args.phase == "verify-index":
        run_phase_verify_index(openviking_url, args.verbose)
    elif args.phase == "expand":
        run_phase_expand(gateway_url, user_id, args.delay, args.verbose)
    elif args.phase == "no-expand":
        run_phase_no_expand(gateway_url, user_id, args.delay, args.verbose)

    if args.log_path:
        scan_expand_log(args.log_path)

    if assertions:
        passed = sum(1 for a in assertions if a["ok"])
        total_a = len(assertions)
        console.print(f"\n[yellow]断言统计: {passed}/{total_a} 通过[/yellow]")

    console.print("\n[yellow]测试结束。[/yellow]")


if __name__ == "__main__":
    main()
