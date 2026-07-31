# OpenViking 发版说明

本文说明 OpenViking 仓库的发版目标、版本与 tag 约定、主要发版流程，以及补发和验证方式。内容以仓库中已追踪的 GitHub Actions、构建配置和包配置为准。

## 发版目标

OpenViking 的发版目标不是单一产物，而是围绕不同使用入口发布一组相互关联的资产：

- `openviking` Python 主包：面向本地运行时、服务端、CLI 及完整功能用户。
- Python SDK `openviking-sdk`：面向只通过 HTTP 调用已有 OpenViking 服务的轻量客户端用户。
- Docker 镜像：面向容器化部署，发布到 GHCR 和 Docker Hub。
- TOS 发布资产：面向源码包、安装脚本和稳定下载路径。
- Rust CLI / npm 包：面向通过 npm 安装 `ov` CLI 的用户。
- OpenClaw / ClawHub 插件：面向 OpenClaw 插件分发渠道。
- VikingBot：当前随 `openviking[bot]` 和官方 Docker 镜像分发；历史独立发版入口单独说明，避免误用。

一次正式主版本发版应确保 Python 包、Docker 镜像和 TOS 资产使用同一个主版本 tag；SDK、CLI、ClawHub 插件则使用各自独立的 tag 或 version 命名空间。

## 版本与 tag 约定

推荐使用以下 tag 约定：

| 产物 | 推荐 tag / version | 说明 |
| --- | --- | --- |
| `openviking` 主包 | `vX.Y.Z` | 主 release tag，例如 `v0.3.26`。 |
| `openviking-sdk` | `python-sdk@X.Y.Z` | SDK 专用 tag，例如 `python-sdk@0.1.3`。 |
| Rust CLI / npm CLI | `cli@X.Y.Z` | CLI 专用 tag，例如 `cli@0.2.0`。 |
| ClawHub 插件 latest | `YYYY.M.D` 或 `YYYY.M.D-N` | 由 workflow 自动生成或手动指定。 |
| ClawHub 插件 dev | `YYYY.M.D-dev.N` | dev channel 使用。 |

主包版本通过 `setuptools_scm` 从 Git tag 解析；正式主发版建议统一使用 `vX.Y.Z`。SDK 版本同样通过 `setuptools_scm` 解析，但只匹配 `python-sdk@*` tag，以避免和主包 tag 混淆。

## 正式主包发版流程

主包正式发版走根目录 GitHub Release：

1. 确认待发布改动已合入目标分支，且 PR / main 分支检查通过。
2. 创建主包 tag，例如 `v0.3.26`。
3. 在 GitHub 上基于该 tag 发布 Release。
4. `03. Release` workflow 会在 Release published 时触发。
5. workflow 复用 `_Build Distribution` 构建 sdist 和多平台 wheel。
6. workflow 将构建产物发布到 PyPI。
7. workflow 构建并推送多架构 Docker 镜像。
8. `Release TOS Upload` workflow 会上传源码 zip 和安装脚本到 TOS。

正式主发版的发布目标包括：

- PyPI：`openviking`
- GHCR：`ghcr.io/<owner>/<repo>`
- Docker Hub：`<dockerhub-user>/openviking`
- TOS：版本化 release 路径和可选 `latest` 稳定路径

## 主包手动构建、测试发布与补发

根目录 release workflow 也支持手动触发，可选择发布目标：

- `none`：只构建，不发布。
- `testpypi`：发布到 TestPyPI。
- `pypi`：发布到 PyPI。
- `both`：同时发布 TestPyPI 和 PyPI。

如需基于已有构建产物补发 Python 包，可使用 `_Publish Distribution` workflow，并传入对应的 build run id。该流程适合发布失败后的补发，不建议作为正常主发版入口。

## Docker 镜像发布与补发

正式主发版时，Docker 镜像由主 release workflow 自动构建并发布。镜像会推送到 GHCR 和 Docker Hub，并在正式 release 下写入版本 tag 与 `latest` tag。

仓库还提供独立的 `Build and Push Docker Image` workflow，适用于：

- 手动指定版本重建镜像。
- `main` 分支镜像构建。
- tag 触发后的镜像补发。

注意：独立 Docker workflow 当前也会在 `v*.*.*` tag push 时自动触发。正式 GitHub Release 的发布口径仍以 `03. Release` workflow 为准；独立 Docker workflow 应视为镜像专用构建或补发路径，避免与正式 release 产物口径混淆。

为避免重复发布，正式版本优先使用主 release workflow；只有镜像补发或特殊验证时才使用独立 Docker workflow。

## TOS 发布资产

TOS 发布流程会生成源码 zip，并上传以下类型资产：

- `releases/<tag>/openviking-<tag>-source.zip`
- Claude Code memory plugin 安装脚本
- Codex memory plugin 安装脚本
- 对应 TOS install 脚本

正式 GitHub Release 会自动触发 TOS 上传。手动补发时可指定 tag，并通过 `update_latest` 决定是否覆盖稳定路径。

如果 TOS 相关 secrets 未配置完整，workflow 会跳过上传并在 step summary 中说明，不会使整个流程失败。

## Python SDK 发版流程

Python SDK 位于 `sdk/python`，PyPI 包名为 `openviking-sdk`。SDK 使用独立 tag 命名空间：

```text
python-sdk@X.Y.Z
```

典型流程：

1. 合入 SDK 相关改动。
2. 创建并推送 tag，例如 `python-sdk@0.1.3`。
3. `Python SDK Release` workflow 被 tag 触发。
4. workflow 使用 `setuptools_scm` 解析 SDK 版本。
5. workflow 校验当前 tag 是否等于 `python-sdk@<resolved-version>`。
6. workflow 构建 `sdk/python` 并发布到 PyPI。

SDK workflow 也支持手动触发，并可选择 `testpypi`、`pypi` 或 `both`。手动触发适合验证和补发；正式 SDK 发版建议使用 `python-sdk@X.Y.Z` tag。

## Rust CLI / npm 发版流程

Rust CLI 的 tag 格式为：

```text
cli@X.Y.Z
```

推送 `cli@*` tag 后，`Rust CLI Build` workflow 会为多个平台构建 `ov` 二进制，并发布 npm 包：

- 平台包：`@openviking/cli-linux-x64`、`@openviking/cli-linux-arm64`、`@openviking/cli-darwin-x64`、`@openviking/cli-darwin-arm64`、`@openviking/cli-win32-x64`
- wrapper 包：`@openviking/cli`

workflow 会把 tag 中的版本写入平台包和 wrapper 包。如果 npm 上已存在同版本包，workflow 会跳过已发布版本。

## OpenClaw / ClawHub 插件发布

OpenClaw 插件通过 `ClawHub release (OpenViking plugin)` workflow 手动发布。输入参数包括：

- `version`：可选；为空时由 workflow 按日期自动生成。
- `channel`：`auto`、`dev` 或 `latest`。
- `changelog`：本次插件发布说明。

workflow 会先解析 channel 和 version，再打包 `examples/openclaw-plugin`，推送生成的 package branch，最后调用 ClawHub 官方 trusted publishing workflow 发布。

推荐做法：

- 正式渠道使用 `latest` 或 `auto`。
- 开发验证使用 `dev`。
- 手动指定 version 时，确保符合对应 channel 的格式要求。

## VikingBot 发布说明

VikingBot 当前不再作为推荐的独立 PyPI 包发版路径维护。现行分发方式是随主包发布：

- Python 安装入口：`pip install "openviking[bot]"`。
- 源码开发入口：`uv pip install -e ".[bot]"`。
- 官方 Docker 镜像默认已包含 VikingBot，可通过 `--without-bot` 或 `OPENVIKING_WITH_BOT=0` 关闭。

根仓库中仍保留 `First Release to PyPI` workflow，但它会在 `bot` 目录执行独立 Python 包构建；当前 `bot/` 目录没有独立的 `pyproject.toml`、`setup.py` 或 `setup.cfg`，因此该 workflow 应视为历史遗留配置，不建议用于新的 VikingBot 发版。

同时，`bot/.github/workflows/release.yml` 位于 `bot` 子目录，它应视为历史上的 bot 子项目或拆分仓库发布参考，不应当作根仓库当前可直接触发的 GitHub Actions workflow。如需恢复独立 `vikingbot` 包，需要先补齐 `bot/` 下独立 Python 包配置、版本策略和发布凭证策略。

## 发版前检查清单

发版前建议逐项确认：

- 待发布改动已合入目标分支。
- CI / PR 检查已通过。
- 版本号未在 PyPI、npm 或 Docker registry 中发布过。
- tag 命名符合对应产物约定。
- Python 包依赖、构建配置和 README 已同步更新。
- Docker Hub、PyPI/TestPyPI、TOS、npm、ClawHub 等发布所需 secrets 或 trusted publishing 配置可用。
- Release notes 已准备好，且说明破坏性变更、迁移步骤和重要修复。

## 发版后验证清单

发版后建议验证：

- PyPI / TestPyPI 上的包版本与 tag 一致。
- `pip install openviking==<version>` 或 `pip install openviking-sdk==<version>` 可成功安装。
- Docker registry 中存在版本 tag 和预期的 `latest` / `main` tag。
- 多架构 Docker manifest 可正常拉取。
- TOS 版本化路径和稳定路径可访问。
- npm 上存在对应 CLI 平台包和 wrapper 包。
- ClawHub 插件 channel 和 version 符合预期。

## 故障处理与补发原则

- PyPI 和 npm 已发布版本通常不可覆盖；如包内容有误，应发布新版本。
- Docker 的 `latest`、`main` 和手动指定 tag 可通过镜像补发 workflow 重建，但应保留已发布版本 tag 的可追溯性。
- TOS 的版本化路径应视为不可变资产；稳定路径可通过手动 workflow 覆盖。
- 如果构建成功但发布失败，优先使用补发 workflow 或手动 dispatch，避免重新创建不同内容的同名 tag。
- 如 tag 命名错误，优先删除错误 tag 并重新创建正确 tag，前提是该 tag 尚未触发不可逆发布。
