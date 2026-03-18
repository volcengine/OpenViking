# OpenViking Web Admin

## 概述

OpenViking Web Admin 是一个完全独立的 React 前端管理界面，通过 HTTP API 与现有 OpenViking 服务器通信，**不修改任何现有后端代码**。

## 技术架构

### 前端技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 框架 | React 18 + TypeScript | 类型安全的组件化开发 |
| 构建工具 | Vite 5.4 | 快速的热模块替换 (HMR) |
| HTTP 客户端 | Axios | 请求拦截器 + 统一错误处理 |
| 路由 | React Router 7 | 客户端路由管理 |
| 状态管理 | Zustand + React Query | 全局状态 + 服务端状态缓存 |
| 样式 | Tailwind CSS | 原子化 CSS 框架 |
| 图表 | Recharts | 数据可视化 |
| 日期处理 | date-fns | 日期格式化 |

### 系统架构

```
┌─────────────────┐         ┌──────────────────────────────────┐
│   Web Browser   │         │         OpenViking API           │
│                 │         │  (Python:1933)                   │
│  - React SPA    │────────▶│  - REST API                      │
│  - UI Components│         │  - Business Logic                │
│  - Local State  │         │  - VikingDB (Vector DB)          │
└─────────────────┘         └──────────────────────────────────┘
         │
         │  X-API-Key Header
         ▼
┌──────────────────────────────────────────────────────────────┐
│                    Service Layer                              │
│  - Axios with Interceptors                                   │
│  - Request/Response Error Handling                           │
│  - React Query Caching                                       │
└──────────────────────────────────────────────────────────────┘
```

## 快速开始

### 前置要求

- Node.js >= 18
- OpenViking 服务器正在运行 (端口 1933)

### 本地开发

```bash
# Terminal 1: 启动 OpenViking API
openviking-server

# Terminal 2: 启动 WebAdmin 前端
cd webadmin
npm install
npm run dev
```

访问：http://localhost:5173

### 生产部署

```bash
# 构建前端
npm run build

# 部署到 ~/.openviking/webadmin/
cp -r dist/* ~/.openviking/webadmin/
```

## 项目结构

```
webadmin/
├── src/                     # React 前端代码
│   ├── services/            # API 服务层
│   │   ├── api.ts          # 基础 HTTP 客户端
│   │   ├── auth.ts         # 认证服务
│   │   ├── monitoring.ts   # 监控服务
│   │   ├── resources.ts    # 资源服务
│   │   ├── sessions.ts     # 会话服务
│   │   ├── filesystem.ts   # 文件系统服务
│   │   ├── search.ts       # 搜索服务
│   │   └── tasks.ts        # 任务服务
│   ├── types/               # TypeScript 类型定义
│   ├── hooks/               # React Query Hooks
│   │   ├── useAuth.ts
│   │   ├── useMonitoring.ts
│   │   ├── useResources.ts
│   │   ├── useSessions.ts
│   │   ├── useFilesystem.ts
│   │   ├── useSearch.ts
│   │   └── useTasks.ts
│   ├── components/          # UI 组件
│   │   ├── common/         # 通用组件 (Layout, Sidebar, etc.)
│   │   └── ui/             # 基础 UI 组件 (Button, Input, Card, etc.)
│   ├── pages/               # 页面组件
│   │   ├── Login.tsx
│   │   ├── Dashboard.tsx
│   │   ├── ResourceManagement.tsx
│   │   ├── ResourceDetail.tsx
│   │   ├── SessionManagement.tsx
│   │   ├── FileExplorer.tsx
│   │   ├── SemanticSearch.tsx
│   │   └── AdminPanel.tsx
│   └── contexts/            # React Context
├── public/                  # 静态资源
├── dist/                    # 构建产物 (npm run build 生成)
├── index.html               # HTML 入口
├── nginx.conf               # Nginx 配置模板
├── vite.config.ts           # Vite 配置
├── tsconfig.json            # TypeScript 配置
├── tailwind.config.js       # Tailwind 配置
├── package.json             # 依赖配置
└── README.md                # 本文档
```

## 功能模块

### Dashboard - 监控仪表盘

显示系统整体状态：
- 系统状态指示灯（健康/警告/错误）
- 资源统计（总数、存储量）
- 队列监控（Embedding、Semantic 队列长度）
- VikingDB 状态（集合数、向量数、存储使用、查询性能）
- VLM 状态（Provider、Model、Token 使用、请求次数）
- 任务监控（进行中、失败任务）
- 队列长度图表

### ResourceManagement - 资源管理

- 资源列表表格（URI、名称、类型、大小、创建时间）
- 添加资源（路径、父 URI、原因）
- 删除资源（带确认）
- 资源详情查看

### ResourceDetail - 资源详情

- 资源内容展示（L0 摘要、L1 概览、L2 完整内容）
- 元信息显示

### SessionManagement - 会话管理

- 会话列表（显示消息数、压缩状态）
- 会话消息展示（用户/AI 区分）
- 添加消息
- 提交会话（提取记忆、压缩内容）

### FileExplorer - 文件浏览器

- 目录浏览（当前 URI、加载按钮）
- 创建目录
- 文件内容预览（L0/L1/L2 三级加载）

### SemanticSearch - 语义搜索

- 语义搜索（向量搜索，支持目标 URI 和限制）
- 内容搜索（正则搜索，支持 URI 和模式）
- 搜索结果展示（URI、类型、级别、得分、摘要）

### AdminPanel - 管理面板

- 账户管理（创建/删除账户）
- 用户管理（注册用户、分配角色、重置 API Key）
- 系统配置（查看配置、重启服务）

## API 接口映射

### 监控相关

| 功能 | API 路径 | 说明 |
|------|---------|------|
| 系统状态 | `GET /api/v1/system/status` | 系统健康状态 |
| 队列状态 | `GET /api/v1/observer/queue` | Embedding/Semantic 队列 |
| VikingDB 状态 | `GET /api/v1/observer/vikingdb` | 向量数据库状态 |
| VLM 状态 | `GET /api/v1/observer/vlm` | VLM Token 使用情况 |

### 资源管理

| 功能 | API 路径 | 说明 |
|------|---------|------|
| 列出资源 | `GET /api/v1/fs/ls` | 查询参数：uri, recursive, limit |
| 添加资源 | `POST /api/v1/resources` | 请求体：path, parent, reason |
| 删除资源 | `DELETE /api/v1/fs` | 查询参数：uri, recursive |
| 读取 L0 | `GET /api/v1/content/abstract` | 查询参数：uri |
| 读取 L1 | `GET /api/v1/content/overview` | 查询参数：uri |
| 读取 L2 | `GET /api/v1/content/read` | 查询参数：uri, offset, limit |

### 会话管理

| 功能 | API 路径 | 说明 |
|------|---------|------|
| 创建会话 | `POST /api/v1/sessions` | - |
| 列出会话 | `GET /api/v1/sessions` | - |
| 会话详情 | `GET /api/v1/sessions/{session_id}` | - |
| 添加消息 | `POST /api/v1/sessions/{session_id}/messages` | 请求体：role, content |
| 提交会话 | `POST /api/v1/sessions/{session_id}/commit` | 查询参数：wait |
| 删除会话 | `DELETE /api/v1/sessions/{session_id}` | - |

### 文件系统

| 功能 | API 路径 | 说明 |
|------|---------|------|
| 列出目录 | `GET /api/v1/fs/ls` | 查询参数：uri, recursive, simple |
| 目录树 | `GET /api/v1/fs/tree` | 查询参数：uri, level_limit |
| 创建目录 | `POST /api/v1/fs/mkdir` | 请求体：uri |

### 搜索

| 功能 | API 路径 | 说明 |
|------|---------|------|
| 语义搜索 | `POST /api/v1/search/find` | 请求体：query, target_uri, limit |
| 内容搜索 | `POST /api/v1/search/grep` | 请求体：uri, pattern |

## 环境配置

### .env 文件

```env
# API 基础 URL
VITE_API_BASE_URL=http://localhost:1933/api/v1
```

### 构建配置

```bash
# 开发模式
npm run dev

# 构建生产版本
npm run build

# 预览生产版本
npm run preview

# TypeScript 检查
npx tsc --noEmit
```

## 认证机制

### 认证流程

1. 用户访问系统，重定向到登录页
2. 输入 API Key
3. 验证成功后，API Key 存储到 localStorage
4. 所有 API 请求通过 Axios 拦截器自动添加 `X-API-Key` header
5. 401 响应自动清空 Token 并跳转登录页

### 安全考虑

- API Key 存储在 localStorage
- 所有 API 请求需要认证
- 401 响应自动处理
- 生产环境建议使用 HTTPS

## 服务管理

### 使用 services.sh 脚本

```bash
# 启动所有服务 (AGFS + OpenViking Server + Web Admin)
~/.openviking/services.sh start

# 仅启动 Web Admin 前端
~/.openviking/services.sh start-webadmin-frontend

# 停止 Web Admin
~/.openviking/services.sh stop-webadmin-frontend

# 查看服务状态
~/.openviking/services.sh status
```

### 服务状态输出

```
=== OpenViking 服务器 ===
状态：运行中
PID: 12345
访问：http://localhost:1933

=== Web Admin 前端 (端口 5173, Vite) ===
状态：运行中 (Vite)
访问：http://0.0.0.0:5173

=== AGFS 服务 ===
状态：运行中
PID: 12344
访问：localhost:1833
```

## Nginx 配置（生产环境）

### WebAdmin Frontend

```nginx
server {
    listen 8173;
    server_name openviking.example.com;

    location / {
        proxy_pass http://localhost:5173;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
    location /api {
        proxy_pass http://localhost:1933;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### OpenViking API

```nginx
server {
    listen 8933;
    server_name openviking.example.com;

    location / {
        proxy_pass http://localhost:1933;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 构建产物

```bash
# 构建后生成
dist/
├── index.html              # 入口 HTML
└── assets/
    ├── index-*.css         # 样式文件 (~20KB)
    └── index-*.js          # JS 文件 (~668KB, gzip ~205KB)
```

## 依赖

```json
{
  "dependencies": {
    "react": "^18.x",
    "react-dom": "^18.x",
    "react-router-dom": "^7.x",
    "axios": "^1.x",
    "zustand": "^4.x",
    "@tanstack/react-query": "^5.x",
    "date-fns": "^3.x",
    "recharts": "^2.x",
    "tailwindcss": "^3.x"
  },
  "devDependencies": {
    "@types/react": "^18.x",
    "@types/react-dom": "^18.x",
    "@vitejs/plugin-react": "^4.x",
    "typescript": "^5.x",
    "vite": "^5.x",
    "postcss": "^8.x",
    "autoprefixer": "^10.x"
  }
}
```

## 开发指南

### 添加新页面

1. 在 `src/pages/` 创建新组件
2. 在 `App.tsx` 添加路由
3. 如有需要，在 `src/services/` 添加对应服务
4. 在 `src/hooks/` 添加对应 hook

### 添加新 UI 组件

1. 在 `src/components/ui/` 创建组件
2. 使用 Tailwind CSS 样式
3. 保持组件无状态（props 驱动）

### 添加新 API 服务

1. 在 `src/services/` 添加服务文件
2. 使用 `apiClient` 发起请求
3. 在 `src/hooks/` 添加对应的 React Query hook

## 已知问题

- 主 JS 文件超过 500KB，可考虑代码分割优化
- 监控数据 30 秒轮询频率，可根据实际情况调整

## 扩展方向

1. **WebSocket 实时推送** - 替代轮询实现实时监控
2. **数据导出** - 监控数据导出为 CSV/PDF
3. **告警配置** - 配置监控告警阈值
4. **自定义仪表板** - 用户自定义监控面板
5. **多租户管理** - 多租户切换和隔离

## 版本信息

- **当前版本**: 0.1.0
- **React 版本**: 18.x
- **TypeScript 版本**: 5.x
- **Vite 版本**: 5.4.x
- **最后更新**: 2026-03-18

## 相关文档

- [架构设计文档](../docs/design/webadmin-architecture.md)
- [实现计划](../PLAN_webadmin.md)
