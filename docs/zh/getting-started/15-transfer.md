# 导入导出

将一份资料目录导出为 OVPack，再导入到新的父目录或另一套 OpenViking 服务，核对文件、元数据和检索结果。

<a id="连接实验账号"></a>

## 连接服务

使用管理员提供的用户或管理员 API Key 配置 CLI，连接 OpenViking 服务。OVPack 导入导出要求服务端启用 API Key 认证，并配置 `server.root_api_key`。

如需在本机搭建服务，可使用独立的配置文件和存储目录，配置模型，将 `server.auth_mode` 设为 `api_key`，并设置 `server.root_api_key`。启动服务并创建账号后，将返回的 `user_key` 填入 CLI 连接配置。具体步骤见[API Key 配置](../guides/04-authentication.md#方案一-api-key)和 [CLI 入门](05-cli-setup.md)。使用新账号时，先在该账号下导入下方样例资料。

## 准备来源

连接服务后，先完成[资料检索](08-resources.md#准备资料)中的导入步骤。本例来源为 `viking://resources/onboarding-zh-v1`，需要来源读权限和目标父目录写权限。

```bash
ov tree viking://resources/onboarding-zh-v1
ov export viking://resources/onboarding-zh-v1 ./lighthouse.ovpack
```

`lighthouse.ovpack` 是所选目录的内容包，包含源文件、语义侧边文件和可迁移索引标量。本页使用默认导出方式，导入时重新生成向量，会调用目标服务的 Embedding 模型。全账号备份与恢复见 [OVPack 参考](../api/14-ovpack.md)。

## 导入新位置

```bash
ov mkdir viking://resources/onboarding-transfer-v1
ov import ./lighthouse.ovpack viking://resources/onboarding-transfer-v1 --vector-mode recompute
```

第二个参数是**目标父目录**。保存返回的实际 root URI，并用它执行下一步。本例通常得到 `viking://resources/onboarding-transfer-v1/onboarding-zh-v1`。

默认冲突策略为 `fail`。若目标 root 已存在，可以选择新的演示父目录；`overwrite` 会替换已有 root，`skip` 会保留已有 root。使用重复演示时的新目录，便于比较前后结果。

## 核对内容和索引

```bash
ov tree viking://resources/onboarding-transfer-v1/onboarding-zh-v1
ov read viking://resources/onboarding-transfer-v1/onboarding-zh-v1/project/project.md
ov read viking://resources/onboarding-transfer-v1/onboarding-zh-v1/meeting/meeting.md
ov wait
```

核对四份源文件的相对路径、正文，以及包内保留的摘要和概览。导入返回后向量处理可能仍在进行，使用 `ov wait` 等待队列处理。它也可能等待服务中的其他排队工作；如果请求超时，可用 `ov status` 检查状态后稍后再试。确认处理完成且无相关错误后，再检索资料。

```bash
ov find "灯塔试点开始前要准备什么？" --uri viking://resources/onboarding-transfer-v1/onboarding-zh-v1
```

完成标志是文件内容一致、必要元数据可读取，并能在新目录检索到相关资料。检索排名与分数会受目标模型及索引配置影响，核对重点是返回内容能否支持问题。

<a id="从-a-服务迁移到-b-服务"></a>

## 数据服务间迁移

例如，将开发环境 A 中的灯塔资料迁移到测试环境 B。数据通过运行 CLI 的电脑中转：从 A 下载 `.ovpack` 文件，再将该文件上传到 B，两套服务之间无需直接连通。

本例迁移选定的资料目录，A 中的原资料会保留。B 的模型、账号和访问凭据需要单独配置；整账号备份与恢复见 [OVPack 参考](../api/14-ovpack.md)。

### 配置两个连接

准备两套支持 OVPack 的服务，认证要求与上文一致。A 的账号需要来源读权限，B 的账号需要目标父目录写权限，B 还需配置可用的 Embedding 模型。迁移前确认 B 支持 A 导出的包格式；导出期间暂停对该样例目录的修改，便于核对内容。

在本机同一个工作目录中创建两个 CLI 配置文件，分别填写实际服务地址和对应账号的 API Key。下面的地址仅作示例：

`ov-a.conf`：

```json
{
  "url": "https://openviking-a.example.com",
  "api_key": "your-service-a-user-key"
}
```

`ov-b.conf`：

```json
{
  "url": "https://openviking-b.example.com",
  "api_key": "your-service-b-user-key"
}
```

在 macOS / Linux 终端中，通过 `OPENVIKING_CLI_CONFIG_FILE` 为每条命令指定连接。先限制配置文件的读取权限，再检查两套服务是否可访问：

```bash
chmod 600 ./ov-a.conf ./ov-b.conf
OPENVIKING_CLI_CONFIG_FILE=./ov-a.conf ov health
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov health
```

后续命令都在这个工作目录中执行。配置文件包含访问凭据，请保存在本机受控位置。

### 从 A 导出

确认 A 中已导入[灯塔资料](08-resources.md#准备资料)，然后将目录下载到本机：

```bash
OPENVIKING_CLI_CONFIG_FILE=./ov-a.conf ov tree viking://resources/onboarding-zh-v1
OPENVIKING_CLI_CONFIG_FILE=./ov-a.conf ov export viking://resources/onboarding-zh-v1 ./lighthouse-a-to-b.ovpack
```

命令成功后，当前目录中会生成 `lighthouse-a-to-b.ovpack`。如果改用另一台电脑连接 B，将这个文件传到该电脑即可；A 的连接凭据留在原电脑上。

### 导入到 B

在 B 中创建一个新的迁移父目录，再导入本地包：

```bash
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov mkdir viking://resources/from-a-v1
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov import ./lighthouse-a-to-b.ovpack viking://resources/from-a-v1 --on-conflict fail --vector-mode recompute
```

这里使用 `recompute`，由 B 的 Embedding 模型重新生成向量，适用于两套环境模型配置不同的情况。导入会调用 B 的模型并产生相应开销。若目标 root 已存在，选择新的父目录后重试。

记录返回的实际 root URI；本例通常为 `viking://resources/from-a-v1/onboarding-zh-v1`。下方命令以这个 URI 为例。

### 在 B 核对结果

先检查目录与正文，再等待 B 的后台向量处理：

```bash
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov tree viking://resources/from-a-v1/onboarding-zh-v1
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov read viking://resources/from-a-v1/onboarding-zh-v1/project/project.md
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov wait
```

`ov wait` 等待队列处理，也可能受 B 中其他排队工作的影响。如果请求超时，查看 B 的状态：

```bash
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov status
```

仍在处理时稍后再次等待；出现错误时检查 B 的模型与存储配置及服务端日志。确认处理完成且无相关错误后，在 B 中检索：

```bash
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov find "灯塔试点开始前要准备什么？" --uri viking://resources/from-a-v1/onboarding-zh-v1
```

| 核对项 | 通过条件 |
| --- | --- |
| 文件与正文 | B 中四份源文件的相对路径和正文与 A 一致 |
| 摘要与概览 | 包内保留的语义侧边文件在 B 中可读取 |
| 检索结果 | 能找到试点准备相关资料，并读取结果 URI 对应的原文 |
| 引用与接入 | 正文中的绝对 URI、服务地址及 Agent 的检索范围已按 B 的实际位置核对 |

核对完成后，将应用或 Agent 的服务地址、凭据和资料检索范围改为 B 的配置，再用同一个问题验证回答及来源。这样可以确认应用已经使用 B 中的资料。A 的原目录可继续保留，用于比对和后续切换安排。

## 包与冲突检查

OVPack 的 manifest 记录文件集合、大小和校验信息。缺失文件、校验不符或不支持的格式版本会使导入失败，此时可以重新导出并核对包的传输过程。向量兼容性、`auto`/`require` 模式及归档校验规则见 [OVPack 参考](../api/14-ovpack.md)。

实验完成后，可保留包作比对；清理时在 Studio 中确认新父目录下的导入 root，再删除演示内容，原来源目录继续可用。
