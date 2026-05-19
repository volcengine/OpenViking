# OpenViking Web Studio

Web Studio 是 OpenViking 的 React/Vite 前端工作台，面向开发者使用。它是一个静态单页应用，只负责呈现资源、检索、会话和运维界面；资源存储、索引、检索、任务队列和 Bot 运行时都来自独立的 OpenViking Server。

当前主要功能区：

- `resources`：资源浏览、上传、导入任务跟踪和文件预览。
- `retrieval`：搜索、find、grep 等检索工作流。
- `sessions`：基于 VikingBot 的会话列表、聊天和 SSE 流式响应。
- `operations`：运行状态、诊断和管理相关视图。

实现规则见 [AGENTS.md](AGENTS.md)，产品入口和信息架构见 [WORKSPACE_IA.md](WORKSPACE_IA.md)。

## 运行依赖

Web Studio 运行时必须连接 OpenViking Server。默认开发地址是：

```text
http://127.0.0.1:1933
```

会话和聊天界面还依赖 OpenViking Server 代理出来的 VikingBot API：

```text
GET  /bot/v1/health
POST /bot/v1/chat
POST /bot/v1/chat/stream
POST /bot/v1/feedback
```

因此，本地开发和部署 Web Studio 时都应使用 `--with-bot` 启动服务端：

```bash
openviking-server --with-bot
```

不带 `--with-bot` 时，资源、搜索、任务、系统状态等核心 API 仍可能可用，但 `/bot/v1/*` 会返回 `503`。这种状态下 Web Studio 的会话页不能提供真实聊天能力。

## 前置条件

- Node.js：需要兼容 Vite 7，推荐使用 Node.js 22 LTS。
- npm：使用 `web-studio/package-lock.json` 安装依赖。
- OpenViking Python 环境：如果要使用会话页，必须安装 `bot` extra。

从仓库源码开发时，在仓库根目录准备后端环境：

```bash
uv pip install -e ".[bot,dev]"
openviking-server init
openviking-server doctor
openviking-server --with-bot
```

使用已发布包时：

```bash
pip install "openviking[bot]"
openviking-server init
openviking-server doctor
openviking-server --with-bot
```

启动前端前，先确认依赖的服务端可用：

```bash
curl http://127.0.0.1:1933/health
curl http://127.0.0.1:1933/ready
curl http://127.0.0.1:1933/bot/v1/health
```

如果 `/bot/v1/health` 返回 503，通常说明服务端没有用 `--with-bot` 启动，或当前 Python 环境没有安装 `openviking[bot]` 依赖。

## 本地开发

安装前端依赖：

```bash
cd web-studio
npm install
```

启动 Vite 开发服务器：

```bash
npm run dev
```

浏览器访问：

```text
http://127.0.0.1:3000
```

默认连接 `http://127.0.0.1:1933`。如果要指定其它服务端作为初始连接地址：

```bash
VITE_OV_BASE_URL=http://127.0.0.1:1933 npm run dev
```

页面右上角的连接设置仍然可以在运行时覆盖 base URL、API key、account ID 和 user ID。

## 连接与鉴权

Web Studio 通过 `src/lib/ov-client` 封装生成的 OpenViking API client。业务代码默认从 `#/lib/ov-client` 取客户端能力，不直接依赖 `src/gen/ov-client`。

浏览器侧连接信息保存位置：

- API key：`sessionStorage`，键名 `ov_console_api_key`。
- base URL、account ID、user ID：`localStorage`，键名 `ov_console_connection`。

请求适配层会按当前连接状态注入：

- `X-API-Key`
- `X-OpenViking-Account`
- `X-OpenViking-User`
- `X-OpenViking-Agent: web-studio`

本地开发模式下，如果服务端没有配置 `root_api_key`，可能允许隐式身份。多租户或生产环境应配置真实 root key 或 user key，并在 Web Studio 的连接设置里填入对应信息。

## 常用命令

| 命令                        | 用途                                            |
| --------------------------- | ----------------------------------------------- |
| `npm run dev`               | 启动 Vite 开发服务器，默认端口 3000。           |
| `npm run build`             | 走 Vite 构建链路并输出 `dist/`。                |
| `npm run preview`           | 本地预览 `dist/` 构建产物。                     |
| `npm run lint`              | 运行当前业务代码范围的 ESLint。                 |
| `npm run format`            | 使用 Prettier 检查格式。                        |
| `npm run check`             | 执行 Prettier 写入和 ESLint 自动修复。          |
| `npm run test`              | 运行 Vitest。                                   |
| `npm run gen-server-client` | 从服务端 OpenAPI 重新生成 `src/gen/ov-client`。 |

## OpenAPI Client 生成

生成代码目录：

```text
src/gen/ov-client
```

不要手动修改生成产物。需要更新客户端时，先启动对应版本的 OpenViking Server，再执行生成命令：

```bash
openviking-server --with-bot
cd web-studio
npm run gen-server-client
```

当前生成脚本固定读取：

```text
http://127.0.0.1:1933/openapi.json
```

脚本会格式化 OpenAPI 文档、整理 operationId，再通过 `@hey-api/openapi-ts` 生成最终 SDK。生成后仍应让业务代码优先走 `src/lib/ov-client`，避免绕过鉴权头注入、telemetry 注入和错误归一化。

## 目录结构

核心目录：

```text
src/routes/              TanStack Router 路由
src/routes/<page>/       顶层页面模块
src/routes/<page>/-*     页面私有组件、hooks、schemas 和工具函数
src/components/ui/       共享基础 UI 组件
src/components/          共享业务组件
src/hooks/               共享 React hooks
src/lib/ov-client/       OpenViking client 运行时适配层
src/gen/ov-client/       OpenAPI 生成客户端
src/i18n/locales/        zh-CN 和 en 翻译资源
src/styles.css           全局样式和设计 token
types/ov-server/         手工补充的服务端 typed result 子集
```

页面私有实现应优先放在对应路由目录下。所有用户可见文案都应同步维护 `src/i18n/locales/en.ts` 和 `src/i18n/locales/zh-CN.ts`。

## 静态部署

Web Studio 的部署产物是 `dist/` 静态文件。OpenViking Server 是独立运行依赖，不能被前端构建替代。

### 1. 启动必需的服务端

生产或类生产环境示例：

```bash
openviking-server --host 0.0.0.0 --port 1933 --with-bot
```

生产环境应在 `ov.conf` 中配置 `server.root_api_key`。如果 Web Studio 和 OpenViking Server 不在同源地址下，还需要把 Web Studio 的访问源加入 `server.cors_origins`。

最小健康检查：

```bash
curl https://ov-api.example.com/health
curl https://ov-api.example.com/ready
curl https://ov-api.example.com/bot/v1/health
```

`/bot/v1/health` 是 Web Studio 部署契约的一部分。只有 core server 健康、但 bot proxy 不健康时，会话界面仍然不可用。

### 2. 构建 Web Studio

构建时写入公开 API 地址：

```bash
cd web-studio
npm ci
VITE_OV_BASE_URL=https://ov-api.example.com npm run build
```

`VITE_OV_BASE_URL` 会作为浏览器中的初始服务端地址。用户仍可以在连接设置中修改它，但生产构建不应依赖本地默认值 `http://127.0.0.1:1933`。

### 3. 发布静态文件

任意静态文件服务器都可以托管 `dist/`，但必须把未知路由回退到 `index.html`，因为 Web Studio 使用客户端路由。

最小 nginx 示例：

```nginx
server {
    listen 80;
    server_name web-studio.example.com;

    root /srv/web-studio/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

如果希望前端和 API 同源，可以把 API 路径反向代理到 OpenViking Server：

```nginx
server {
    listen 80;
    server_name ov.example.com;

    root /srv/web-studio/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:1933;
    }

    location /bot/ {
        proxy_pass http://127.0.0.1:1933;
    }

    location /health {
        proxy_pass http://127.0.0.1:1933;
    }

    location /ready {
        proxy_pass http://127.0.0.1:1933;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

同源部署时构建：

```bash
VITE_OV_BASE_URL=https://ov.example.com npm run build
```

### 4. 同 host 子路径部署

如果希望 Web Studio 部署在同一个 host 的某个路径下，例如：

```text
https://ov.example.com/web-studio/
```

API 仍然由同一个 host 的根路径代理到 OpenViking Server：

```text
https://ov.example.com/api/*
https://ov.example.com/bot/*
https://ov.example.com/health
https://ov.example.com/ready
```

构建时需要同时设置：

- `VITE_OV_BASE_URL=https://ov.example.com`：浏览器请求 OpenViking API 的根地址。
- `--base=/web-studio/`：Vite 静态资源和前端 Router 的挂载路径。

构建命令：

```bash
cd web-studio
npm ci
VITE_OV_BASE_URL=https://ov.example.com npm run build -- --base=/web-studio/
```

把 `dist/` 发布到服务器目录，例如：

```text
/srv/web-studio
```

nginx 示例：

```nginx
server {
    listen 80;
    server_name ov.example.com;

    root /srv;

    location = /web-studio {
        return 301 /web-studio/;
    }

    location /web-studio/ {
        try_files $uri $uri/ /web-studio/index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:1933;
    }

    location /bot/ {
        proxy_pass http://127.0.0.1:1933;
    }

    location /health {
        proxy_pass http://127.0.0.1:1933;
    }

    location /ready {
        proxy_pass http://127.0.0.1:1933;
    }
}
```

这个模式下，不要把 `VITE_OV_BASE_URL` 设置成 `https://ov.example.com/web-studio`。`/web-studio/` 只是前端静态页面的挂载路径；OpenViking API 仍应从 `https://ov.example.com/api/*` 和 `https://ov.example.com/bot/*` 访问。

### 5. Docker 服务端依赖

官方 OpenViking 镜像可以作为 Web Studio 依赖的 API server：

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -p 8020:8020 \
  -v ~/.openviking:/app/.openviking \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

官方镜像默认会启动 VikingBot。用于 Web Studio 会话页时，不要传 `--without-bot`，也不要设置 `OPENVIKING_WITH_BOT=0`。

Web Studio 静态文件仍需单独构建和托管，除非你的部署镜像或平台显式把 `web-studio/dist` 集成进去。

## 常见问题

### `/bot/v1/*` 返回 503

服务端没有启用 `--with-bot`，或者 VikingBot gateway 启动失败。安装 bot 依赖后重启：

```bash
uv pip install -e ".[bot,dev]"
openviking-server --with-bot
```

服务端日志中应能看到 `Bot API proxy enabled`。

### 生成 client 时拉不到 OpenAPI

`npm run gen-server-client` 当前固定读取 `http://127.0.0.1:1933/openapi.json`。先启动本地 server，并确保这个 server 版本就是前端要适配的目标版本。

### 浏览器出现 CORS 错误

如果 Web Studio 和 OpenViking Server 不同源，需要在 `ov.conf` 的 `server.cors_origins` 中加入 Web Studio 的访问源并重启 server。同源部署时，反向代理 `/api/`、`/bot/`、`/health` 和 `/ready` 到 OpenViking Server。

### 连接弹窗反复打开

通常是 API key 无效、key 与当前 server 不匹配，或填写的 `accountId`/`userId` 和 key 的权限范围不一致。先用相同 server URL 和 key 直接请求一个 API 验证，再更新 Web Studio 连接设置。

## 相关文档

- [AGENTS.md](AGENTS.md)：Web Studio 本地实现规则。
- [WORKSPACE_IA.md](WORKSPACE_IA.md)：Web Studio 信息架构和产品规划。
- [VIKINGBOT_STATUS.md](VIKINGBOT_STATUS.md)：sessions/chat 实现说明。
- [OpenViking server deployment](../docs/en/guides/03-deployment.md)：服务端部署说明。
- [VikingBot validation with OpenViking Server](../bot/docs/vikingbot-phase1-validation-with-openviking-server.md)：Bot proxy 验证流程。
