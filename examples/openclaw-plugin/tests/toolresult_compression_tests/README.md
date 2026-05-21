# Tool Result Compression Bench Tests

这个目录放的是用于手工回归 OpenClaw + OpenViking 工具结果压缩链路的轻量 benchmark 脚本和测试用例。

## 前置依赖

运行前需要先把下面 3 个仓库下载到 OpenClaw workspace 中，并保证路径和测试用例里的 prompt 一致：

| 仓库 | 用例 | 期望路径 |
| --- | --- | --- |
| Kubernetes | `test1.json`, `test2.json` | `/root/.openclaw/workspace/kubernetes` |
| OpenViking | `test3.json`, `test4.json` | `/root/.openclaw/workspace/OpenViking` |
| openclaw | `test5.json` | `/root/.openclaw/workspace/openclaw` |

如果本机 workspace 根目录不同，可以创建软链接到上述路径，或只在本地修改对应 `test*.json` 中的路径后再运行。

同时需要确保：

- `openclaw` 命令可用，且已安装/启用当前 OpenViking openclaw plugin。
- OpenViking 服务和 OpenClaw 配置已指向同一个可用的 OV 实例。
- 运行账号能读取 workspace 中的 3 个仓库。

## 运行单个用例

在本目录执行：

```bash
node run-sccs-bench.mjs run \
  --prompts test1.json \
  --label test1 \
  --session-id "bench-quick-$(date +%s)-ov-01-test1" \
  --timeout-sec 600 \
  --out-dir "bench-results-quick/test1-ov-01"
```

可将 `test1.json` 替换为 `test2.json` 到 `test5.json`。

## 批量运行示例

下面是一个可复制的最小 wrapper。将占位路径替换为当前机器上的 OV session 存储目录；如果只有一个存储目录，保留一个即可。

```bash
#!/bin/bash
set -euo pipefail

cd "<OpenViking>/examples/openclaw-plugin/tests/toolresult_compression_tests"

OUT_ROOT="bench-results-quick"
OV_SESSION_DIR_A="<ov-session-store-dir-a>"
OV_SESSION_DIR_B="<ov-session-store-dir-b>"
OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"

run_test() {
    local test_name="$1"
    local mode="$2"
    local i="$3"

    rm -rf "${OV_SESSION_DIR_A:?}/"*
    rm -rf "${OV_SESSION_DIR_B:?}/"*

    node run-sccs-bench.mjs run \
        --prompts "${test_name}.json" \
        --label "$test_name" \
        --session-id "bench-quick-$(date +%s)-${mode}-${i}-${test_name}" \
        --timeout-sec 600 \
        --out-dir "${OUT_ROOT}/${test_name}-${mode}-$(printf '%02d' "$i")"

    rm -rf "${OPENCLAW_STATE_DIR}/memory"
}

run_test "test1" ov 1
```

## 输出

每次运行会在 `--out-dir` 下生成本轮的 JSON/CSV 报告和 OpenClaw session 产物。`bench-results-*` 目录是本地运行产物，不需要提交。

