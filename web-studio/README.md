# Web Studio

Web Studio 是 OpenViking 当前的前端工作区，用来承接旧控制台能力的迁移与整理。当前实现不是 TanStack Start SSR 项目，而是一个基于 Vite 的 React 单页应用，默认入口会跳转到遗留控制台页面集合。

## 当前状态

当前已落地的页面都位于 legacy 路由下：

- / 会重定向到 /legacy/data
- /legacy/access 用于配置连接信息和身份请求头
- /legacy/data 用于文件系统浏览、内容读取、资源导入、检索与会话相关操作
- /legacy/ops 用于租户、用户、密钥与系统状态相关的管理操作

这套界面保留了旧控制台的入口形态，但请求链路已经切到新的 ov-client 适配层，直接访问真实 OpenViking HTTP Server。

## 技术栈

- React 19
- Vite 7
- TanStack Router 文件路由
- TanStack Query
- Tailwind CSS v4
- shadcn/ui
- lucide-react
- Axios
- Vitest

## 本地开发

安装依赖：

```bash
npm install
```

启动开发服务器：

```bash
npm run dev
```

默认端口是 3000。

生产构建：

```bash
npm run build
```

预览构建产物：

```bash
npm run preview
```

质量检查：

```bash
npm run lint
npm run format
npm run check
```

运行测试：

```bash
npm run test
```

## 后端依赖与连接方式

前端默认连接地址是 http://127.0.0.1:1933。这个地址同时用于：

- 运行时 API 请求
- OpenAPI 文档拉取与客户端生成

连接信息分两类保存：

- X-API-Key 保存在 sessionStorage，键名为 ov_console_api_key
- baseUrl、accountId、userId、agentId 保存在 localStorage

页面初始化时会读取这些值，并同步到 src/lib/ov-client/client.ts 导出的全局 ovClient。

如果需要通过环境变量覆盖默认服务地址，可以设置 VITE_OV_BASE_URL。但当前代码里已经显式创建了 ovClient 默认实例指向 http://127.0.0.1:1933，因此本地联调通常直接通过页面中的 Access 配置即可。

## API Client 结构

前端请求层分成两层。

### OpenAPI 生成层

- 目录：src/gen/ov-client
- 来源：后端 OpenAPI 文档自动生成
- 规则：禁止手动修改生成产物

生成命令：

```bash
npm run gen-server-client
```

这条命令会执行以下流程：

1. 从 http://127.0.0.1:1933/openapi.json 拉取最新 OpenAPI 文档
2. 使用 openapi-format 格式化中间文件
3. 运行 script/gen-server-client/polishOpId.js 清洗 operationId
4. 使用 @hey-api/openapi-ts 生成最终 SDK

相关文件：

- script/gen-server-client/gen-server-client.sh
- script/gen-server-client/oaf-generate-conf.json
- script/gen-server-client/polishOpId.js
- script/gen-server-client/generate/openapi-formatted.json
- src/gen/ov-client

### 前端适配层

- 目录：src/lib/ov-client
- 作用：为生成 SDK 补齐前端运行时约定，而不是重写接口定义

适配层当前职责：

- 注入 X-API-Key、X-OpenViking-Account、X-OpenViking-User、X-OpenViking-Agent
- 为部分 POST 请求自动补 telemetry: true
- 将服务端错误、HTTP 错误、网络错误统一归一化为 OvClientError
- 提供 getOvResult() 等调用辅助方法

适配层当前不做这些事：

- 不复刻旧 console BFF
- 不引入 /console/api/v1 或 /ov/... 别名
- 不实现 runtime/capabilities
- 不直接修改生成层代码

业务代码应优先从 #/lib/ov-client 导入，而不是直接从 #/gen/ov-client 导入。

## 页面与目录

当前核心目录如下：

```text
web-studio/
├── public/                        # 静态资源
├── script/gen-server-client/      # OpenAPI 客户端生成脚本
├── src/
│   ├── components/
│   │   ├── legacy/                # 旧控制台迁移页面组件
│   │   └── ui/                    # shadcn/ui 基础组件
│   ├── gen/ov-client/             # OpenAPI 生成代码，禁止手改
│   ├── hooks/                     # 通用 hooks
│   ├── lib/
│   │   ├── legacy/                # legacy 页面所需连接与路由工具
│   │   └── ov-client/             # 生成 SDK 上方的前端适配层
│   ├── routes/                    # TanStack Router 文件路由
│   ├── main.tsx                   # 应用入口，接入 Router 和 QueryClient
│   ├── routeTree.gen.ts           # 路由生成文件，禁止手改
│   ├── router.tsx                 # Router 工厂
│   └── styles.css                 # 全局样式与主题变量
├── AGENTS.md                      # 本工作区内的 agent 约束
└── README.md
```

## 路由约定

- 路由文件放在 src/routes
- src/routes/__root.tsx 提供全局样式入口与 devtools 容器
- src/routes/index.tsx 当前只负责重定向到 /legacy/data
- 遗留页面路由保持轻量，复杂逻辑应下沉到 src/components/legacy
- src/routeTree.gen.ts 由路由工具生成，不要手动编辑

## UI 与样式约定

- 基础组件放在 src/components/ui
- 业务页面组件放在 src/components/legacy 或其他业务目录
- 全局设计 token 和基础样式集中在 src/styles.css
- 已配置路径别名 #/...，新增代码时优先沿用

如果需要新增 shadcn/ui 组件，在当前目录执行：

```bash
npx shadcn@latest add button card dialog
```

## 开发建议

- 新功能优先判断是继续承接 legacy 页面，还是抽成新的共享业务组件
- 路由文件尽量只保留参数解析、数据装配和页面入口
- 页面逻辑里需要请求后端时，统一走 #/lib/ov-client
- 生成代码变更后，优先运行 npm run build 至少验证一次
- 如果接口签名变化，先重新执行 npm run gen-server-client，不要手工补丁生成文件

## 常见维护动作

更新客户端：

```bash
npm run gen-server-client
```

做完改动后做一次完整检查：

```bash
npm run build
npm run test
```

如果只是校验格式和 lint：

```bash
npm run check
```