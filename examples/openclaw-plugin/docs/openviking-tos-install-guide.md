# OpenViking TOS 安装包发布与安装说明

> 更新时间：2026-06-03
> 发布目录：`latest`（默认）与可指定日期目录（示例：`2026.6.3`）
> 发布内容：`install.sh`、`openviking.tgz`、`manifest.json`

## 1. 本次发布结论

当前发布协议只发布三个文件：一个兼容多区域的 `install.sh`，一个插件压缩包 `openviking.tgz`，一个描述文件 `manifest.json`。三个文件会覆盖上传到 4 个 TOS bucket，并设置对象 ACL 为公开读（public-read）。每个 bucket 都先上传日期目录；日期目录可用 `--release-dir <yyyy.m.d>` 指定，未指定时按运行当天动态生成，不能固定死某个日期。`latest` 目录只有在组件稳定后显式指定 `--publish-latest` 才上传：

- `latest/install.sh`
- `latest/openviking.tgz`
- `latest/manifest.json`
- `2026.6.3/install.sh`
- `2026.6.3/openviking.tgz`
- `2026.6.3/manifest.json`

安装脚本默认安装 `latest/openviking.tgz`；如需安装固定日期版本，可添加 `--date 2026.6.3`。

如果目标 bucket 不存在，上传脚本会先自动创建 bucket，再上传对象。本文中的验证命令均使用 dry-run 或本地语法/单测校验，不执行真实 TOS 上传。

## 2. 域名规则

`install.sh` 默认使用内部域名，适用于内部网络环境：

```text
ivolces.com
```

公网测试或公网安装时，需要显式添加：

```bash
--external
```

此时脚本会使用公网域名：

```text
volces.com
```

## 3. 已发布的公网下载地址

### 3.1 arkclaw-ov-cn-beijing

```text
https://arkclaw-ov-cn-beijing.tos-cn-beijing.volces.com/latest/install.sh
https://arkclaw-ov-cn-beijing.tos-cn-beijing.volces.com/latest/openviking.tgz
https://arkclaw-ov-cn-beijing.tos-cn-beijing.volces.com/latest/manifest.json
https://arkclaw-ov-cn-beijing.tos-cn-beijing.volces.com/2026.6.3/install.sh
https://arkclaw-ov-cn-beijing.tos-cn-beijing.volces.com/2026.6.3/openviking.tgz
https://arkclaw-ov-cn-beijing.tos-cn-beijing.volces.com/2026.6.3/manifest.json
```

### 3.2 arkclaw-ov-cn-guangzhou

```text
https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.volces.com/latest/install.sh
https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.volces.com/latest/openviking.tgz
https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.volces.com/latest/manifest.json
https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.volces.com/2026.6.3/install.sh
https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.volces.com/2026.6.3/openviking.tgz
https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.volces.com/2026.6.3/manifest.json
```

### 3.3 arkclaw-ov-cn-shanghai

```text
https://arkclaw-ov-cn-shanghai.tos-cn-shanghai.volces.com/latest/install.sh
https://arkclaw-ov-cn-shanghai.tos-cn-shanghai.volces.com/latest/openviking.tgz
https://arkclaw-ov-cn-shanghai.tos-cn-shanghai.volces.com/latest/manifest.json
https://arkclaw-ov-cn-shanghai.tos-cn-shanghai.volces.com/2026.6.3/install.sh
https://arkclaw-ov-cn-shanghai.tos-cn-shanghai.volces.com/2026.6.3/openviking.tgz
https://arkclaw-ov-cn-shanghai.tos-cn-shanghai.volces.com/2026.6.3/manifest.json
```

### 3.4 arkclaw-ov

```text
https://arkclaw-ov.tos-cn-beijing.volces.com/latest/install.sh
https://arkclaw-ov.tos-cn-beijing.volces.com/latest/openviking.tgz
https://arkclaw-ov.tos-cn-beijing.volces.com/latest/manifest.json
https://arkclaw-ov.tos-cn-beijing.volces.com/2026.6.3/install.sh
https://arkclaw-ov.tos-cn-beijing.volces.com/2026.6.3/openviking.tgz
https://arkclaw-ov.tos-cn-beijing.volces.com/2026.6.3/manifest.json
```

## 4. 推荐安装方式

### 4.1 内部网络安装 latest（默认）

在内部网络环境中，直接下载对应区域的 `install.sh` 并执行即可。脚本默认使用内部域名并安装 `latest/openviking.tgz`。

以广州区域为例：

```bash
wget https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.ivolces.com/latest/install.sh -O install.sh
bash install.sh --region cn-guangzhou
```

### 4.2 公网安装 latest

公网环境需要使用公网下载地址，并在执行脚本时加 `--external`。

以广州区域为例：

```bash
wget https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.volces.com/latest/install.sh -O install.sh
bash install.sh --external --region cn-guangzhou
```

### 4.3 安装固定日期版本

如需安装 `2026.6.3` 固定版本：

```bash
wget https://arkclaw-ov-cn-guangzhou.tos-cn-guangzhou.volces.com/2026.6.3/install.sh -O install.sh
bash install.sh --external --region cn-guangzhou --date 2026.6.3
```

### 4.4 指定 bucket 安装

如果使用默认 bucket `arkclaw-ov`，可指定 bucket：

```bash
wget https://arkclaw-ov.tos-cn-beijing.volces.com/latest/install.sh -O install.sh
bash install.sh --external --region cn-beijing --bucket arkclaw-ov
```

### 4.5 自定义 TOS Base URL

如果希望完全指定下载根路径，可使用 `--tos-base-url`：

```bash
bash install.sh --tos-base-url https://arkclaw-ov.tos-cn-beijing.volces.com
```

默认下载：

```text
https://arkclaw-ov.tos-cn-beijing.volces.com/latest/openviking.tgz
```

如果需要日期目录：

```bash
bash install.sh --tos-base-url https://arkclaw-ov.tos-cn-beijing.volces.com --date 2026.6.3
```

对应下载：

```text
https://arkclaw-ov.tos-cn-beijing.volces.com/2026.6.3/openviking.tgz
```

## 5. install.sh 参数速查

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `--internal` | 使用内部域名 `ivolces.com` | 默认开启 |
| `--external` | 使用公网域名 `volces.com` | 关闭 |
| `--latest` | 安装 `latest/openviking.tgz` | 默认开启 |
| `--date <date>` | 安装日期目录版本，如 `2026.6.3/openviking.tgz` | 无 |
| `--release-path <path>` | 安装自定义目录下的 `openviking.tgz` | `latest` |
| `--region <region>` | 指定区域，如 `cn-beijing` / `cn-guangzhou` / `cn-shanghai` | 自动识别，失败时为 `cn-beijing` |
| `--bucket <bucket>` | 指定 bucket 名称 | `arkclaw-ov` |
| `--tos-base-url <url>` | 完整指定 TOS 根 URL | 自动按 bucket/region/domain 生成 |
| `--manifest-url <url>` | 完整指定 manifest URL | 自动按 bucket/region/domain/release-path 生成 |
| `--source local` | 从 `install.sh` 同目录安装本地 `openviking.tgz` | 远端下载 |
| `--tarball <path>` | 从指定本地 tgz 安装 | 无 |
| `--verify-only` | 仅下载/校验，不安装 | 关闭 |
| `--dry-run` | 打印动作，不执行下载/安装 | 关闭 |
| `--no-restart` | 安装后不重启 OpenClaw gateway | 默认会重启 |

## 6. 上传脚本说明

上传脚本路径：

```text
scripts/upload_tos.py
```

执行上传（真实上传，需要 `TEAM_TEST_AK` / `TEAM_TEST_SK`）：

```bash
python3 scripts/upload_tos.py --release-dir 2026.6.3
```

只验证脚本路径、对象 key、bucket 与 latest 策略，不真实上传 TOS：

```bash
python3 scripts/upload_tos.py --release-dir 2026.6.3 --dry-run
```

不指定 `--release-dir` 时，脚本默认使用运行当天的 `yyyy.m.d` 作为日期目录。

组件稳定后再发布 latest：

```bash
python3 scripts/upload_tos.py --release-dir 2026.6.3 --publish-latest
```

完整发布入口会先构建三文件产物，再调用上传脚本：

```bash
TEAM_TEST_AK=... TEAM_TEST_SK=... scripts/release-to-tos.sh --release-dir 2026.6.3
TEAM_TEST_AK=... TEAM_TEST_SK=... scripts/release-to-tos.sh --release-dir 2026.6.3 --publish-latest
```

仅做发布脚本正确性验证、不上传 TOS：

```bash
scripts/release-to-tos.sh --release-dir 2026.6.3 --dry-run
```

上传脚本会读取环境变量：

```text
TEAM_TEST_AK
TEAM_TEST_SK
```

上传对象：

```text
install.sh
openviking.tgz
manifest.json
```

上传路径：

```text
2026.6.3/install.sh
2026.6.3/openviking.tgz
2026.6.3/manifest.json
latest/install.sh       # 仅 --publish-latest 时上传
latest/openviking.tgz   # 仅 --publish-latest 时上传
latest/manifest.json    # 仅 --publish-latest 时上传
```

上传目标 bucket：

```text
arkclaw-ov-cn-beijing
arkclaw-ov-cn-guangzhou
arkclaw-ov-cn-shanghai
arkclaw-ov
```

如果 bucket 不存在，上传脚本会自动创建对应 bucket。

上传时已设置对象 ACL：

```text
public-read
```

## 7. 本次验证结果

### 7.1 本地脚本测试

本地只验证脚本正确性，不做真实 TOS 上传。已通过以下测试：

```bash
npx vitest run tests/ut/tos-release-contract.test.ts
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_upload_tos.py
bash -n scripts/install.sh
bash -n scripts/release-to-tos.sh
node --check scripts/generate-release-manifest.mjs
node --check scripts/tos-release-client.mjs
```

测试覆盖：

- 默认使用 `latest/openviking.tgz`
- `--date 2026.6.3` 使用日期目录
- 默认内部域名为 `ivolces.com`
- `--external` 使用公网域名 `volces.com`
- 上传脚本默认只上传指定日期目录，`--publish-latest` 时才上传 `latest`
- 不指定 `--release-dir` 时动态使用当天日期目录
- bucket 不存在时自动创建 bucket
- 上传对象使用 `public-read` ACL

## 8. 常见问题

### 8.1 为什么公网测试必须加 --external？

因为 `ivolces.com` 是内部域名，在当前公网测试环境无法连通。加 `--external` 后，脚本会改用 `volces.com` 公网域名。

### 8.2 默认不指定日期时安装哪个版本？

默认安装 `latest/openviking.tgz`。

### 8.3 如何回滚到固定日期目录？

使用 `--date`：

```bash
bash install.sh --date 2026.6.3
```

公网环境：

```bash
bash install.sh --external --date 2026.6.3
```

### 8.4 覆盖上传是否安全？

本次需求明确要求同路径已有文件直接覆盖。上传脚本未开启禁止覆盖，并在每次上传时设置 public-read ACL。
