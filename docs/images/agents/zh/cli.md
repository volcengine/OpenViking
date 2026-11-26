
按步骤复制以下命令到你的终端，运行即可完成安装、配置和使用 OpenViking CLI：

### 步骤1：安装 OpenViking CLI，并进入配置流程：

```bash
npm i -g @openviking/cli && ov config
```


### 步骤2：配置连接

选择托管服务预设后，向导会自动设置 Base URL。

- Base URL：如果向导要求填写地址，请将以下地址复制到终端：
```text
{{OPENVIKING_BASE_URL}}
```
- API Key: 把 API Key 粘贴到终端

{{OPENVIKING_API_KEY_BLOCK}}


### 步骤3：配置完成后，可运行以下命令查看 CLI 用法：

```bash
ov config validate
ov health
ov --help
```
