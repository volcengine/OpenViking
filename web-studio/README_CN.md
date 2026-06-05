# OpenViking Web Studio

[English](README.md) / 中文

Web Studio 是 OpenViking 的 React/Vite 前端工作台，面向开发者使用。它是一个静态单页应用，用于资源管理、检索、Bot 会话和运维诊断。

Web Studio 不内嵌 OpenViking 的存储、索引、检索、任务队列或 VikingBot 运行时。它必须连接一个正在运行的 OpenViking Server。

## 运行契约

默认本地服务端地址：

```text
http://127.0.0.1:1933
```

会话界面依赖 OpenViking Server 代理出来的 VikingBot API：

```text
GET  /bot/v1/health
POST /bot/v1/chat
POST /bot/v1/chat/stream
POST /bot/v1/feedback
```

本地开发和部署时，都应使用 bot 支持启动 OpenViking Server：

```bash
openviking-server --with-bot
```

不带 `--with-bot` 时，资源、搜索、任务、系统状态等核心 API 仍可能可用，但 `/bot/v1/*` 会返回 `503`；sessions 页面无法提供真实聊天能力。

## 快速开始

### 1. 启动服务端

从仓库根目录开发：

```bash
uv pip install -e ".[bot,dev]"
openviking-server init
openviking-server doctor
openviking-server --with-bot
```

或使用已发布包：

```bash
pip install "openviking[bot]"
openviking-server init
openviking-server doctor
openviking-server --with-bot
```

检查 Web Studio 必需的服务端能力：

```bash
curl http://127.0.0.1:1933/health
curl http://127.0.0.1:1933/ready
curl http://127.0.0.1:1933/bot/v1/health
```

### 2. 启动 Web Studio

```bash
cd web-studio
npm install
npm run dev
```

浏览器访问：

```text
http://127.0.0.1:3000
```

如果要覆盖初始服务端地址：

```bash
VITE_OV_BASE_URL=http://127.0.0.1:1933 npm run dev
```

连接弹窗仍可以在运行时覆盖 server URL、API key、account ID 和 user ID。

## 连接与鉴权

业务代码应使用 `src/lib/ov-client` 下的适配层，而不是直接从 `src/gen/ov-client` 导入。适配层集中处理 base URL、鉴权头、telemetry 默认值和错误归一化。

浏览器存储：

| 值                      | 存储             | 键名                    |
| ----------------------- | ---------------- | ----------------------- |
| API key                 | `sessionStorage` | `ov_console_api_key`    |
| Base URL、account、user | `localStorage`   | `ov_console_connection` |

请求适配层会注入：

- `X-API-Key`
- `X-OpenViking-Account`
- `X-OpenViking-User`

生产或多租户部署应在 OpenViking Server 中配置真实的 `server.root_api_key` 或 user key，并在 Web Studio 中填写匹配的连接信息。

## 常用命令

| 命令                        | 用途                                            |
| --------------------------- | ----------------------------------------------- |
| `npm run dev`               | 启动 Vite 开发服务器，端口 3000。               |
| `npm run build`             | 构建静态生产产物到 `dist/`。                    |
| `npm run preview`           | 本地预览 `dist/` 构建产物。                     |
| `npm run lint`              | 运行当前业务代码范围的 ESLint。                 |
| `npm run format`            | 用 Prettier 检查格式。                          |
| `npm run check`             | 运行 Prettier 写入和 ESLint 自动修复。          |
| `npm run test`              | 运行 Vitest。                                   |
| `npm run gen-server-client` | 从服务端 OpenAPI 重新生成 `src/gen/ov-client`。 |

## 生成的 OpenAPI Client

生成代码目录：

```text
src/gen/ov-client
```

不要手动修改生成产物。需要从目标 OpenViking Server 版本重新生成：

```bash
openviking-server --with-bot
cd web-studio
npm run gen-server-client
```

当前生成脚本读取：

```text
http://127.0.0.1:1933/openapi.json
```

脚本会格式化 OpenAPI 文档、整理 operation ID，并运行 `@hey-api/openapi-ts`。

## 项目结构

```text
src/routes/              TanStack Router 路由
src/routes/<page>/       顶层页面模块
src/routes/<page>/-*     页面私有组件、hooks、schemas 和工具函数
src/components/ui/       共享基础 UI 组件
src/components/          共享业务组件
src/hooks/               共享 React hooks
src/lib/ov-client/       OpenViking client 运行时适配层
src/gen/ov-client/       OpenAPI 生成客户端
src/i18n/locales/        en 和 zh-CN 翻译资源
src/styles.css           全局样式和设计 token
types/ov-server/         手工补充的服务端 typed result 子集
```

页面私有实现应放在对应路由目录下。所有用户可见文案都应同步维护 `src/i18n/locales/en.ts` 和 `src/i18n/locales/zh-CN.ts`。

## 部署

Web Studio 的部署产物是 `dist/` 静态文件。OpenViking Server 仍然是独立运行依赖。

### 1. 启动必需的服务端

生产或类生产环境示例：

```bash
openviking-server --host 0.0.0.0 --port 1933 --with-bot
```

生产环境应在 `ov.conf` 中配置 `server.root_api_key`。如果 Web Studio 和 OpenViking Server 不同源，需要把 Web Studio 的访问源加入 `server.cors_origins`。

最小健康检查：

```bash
curl https://ov-api.example.com/health
curl https://ov-api.example.com/ready
curl https://ov-api.example.com/bot/v1/health
```

`/bot/v1/health` 是 Web Studio 部署契约的一部分。只有 core server 健康但 bot proxy 不健康时，会话界面仍然不可用。

### 2. 构建静态文件

独立前端域名部署：

```bash
cd web-studio
npm ci
VITE_OV_BASE_URL=https://ov-api.example.com npm run build
```

`VITE_OV_BASE_URL` 是浏览器中的初始 OpenViking API origin。用户仍可以在连接弹窗中修改它。

### 3. 独立 host 部署

示例 URL：

```text
https://web-studio.example.com/
```

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

### 4. 同 host 根路径部署

示例 URL：

```text
https://ov.example.com/
```

将 OpenViking API 路径反向代理到 server，并把 Web Studio 发布在 `/`：

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

构建命令：

```bash
VITE_OV_BASE_URL=https://ov.example.com npm run build
```

### 5. 同 host 子路径部署

示例 URL：

```text
https://ov.example.com/web-studio/
```

这种布局下，Web Studio 挂载在 `/web-studio/`，OpenViking API 仍保留在 host 根路径：

```text
https://ov.example.com/api/*
https://ov.example.com/bot/*
https://ov.example.com/health
https://ov.example.com/ready
```

构建时同时传入两个值：

```bash
cd web-studio
npm ci
VITE_OV_BASE_URL=https://ov.example.com npm run build -- --base=/web-studio/
```

含义：

- `VITE_OV_BASE_URL=https://ov.example.com`：浏览器请求 API 的 origin。
- `--base=/web-studio/`：Vite 静态资源 base 和 TanStack Router 挂载路径。

把 `dist/` 发布到：

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

不要把 `VITE_OV_BASE_URL` 设置成 `https://ov.example.com/web-studio`。`/web-studio/` 只是前端挂载路径；OpenViking API 请求仍应访问 `https://ov.example.com/api/*` 和 `https://ov.example.com/bot/*`。

### 6. Docker 服务端依赖

官方 OpenViking 镜像可以作为 API server 依赖：

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

Web Studio 静态文件仍需单独构建和托管，除非你的部署镜像或平台显式把 `web-studio/dist` 打包进去。

## 常见问题

### `/bot/v1/*` 返回 503

服务端没有用 `--with-bot` 启动，或者 VikingBot gateway 启动失败。安装 bot 依赖后重启：

```bash
uv pip install -e ".[bot,dev]"
openviking-server --with-bot
```

服务端日志中应能看到 `Bot API proxy enabled`。

### 生成 client 时拉不到 OpenAPI

`npm run gen-server-client` 读取 `http://127.0.0.1:1933/openapi.json`。先启动本地 server，并确保这个 server 版本就是前端要适配的目标版本。

### 浏览器出现 CORS 错误

如果 Web Studio 和 OpenViking Server 不同源，需要在 `ov.conf` 的 `server.cors_origins` 中加入 Web Studio 的访问源并重启 server。同源部署时，反向代理 `/api/`、`/bot/`、`/health` 和 `/ready` 到 OpenViking Server。

### 连接弹窗反复打开

通常是 API key 缺失或无效、key 属于另一个 server，或选择的 account/user 与 key 的权限范围不匹配。先用相同 server URL 和 key 直接请求一个 API 验证，再更新 Web Studio 连接设置。

## 相关文档

- [OpenViking server deployment](../docs/en/guides/03-deployment.md)：服务端部署说明。
- [VikingBot validation with OpenViking Server](../bot/docs/vikingbot-phase1-validation-with-openviking-server.md)：Bot proxy 验证流程。
