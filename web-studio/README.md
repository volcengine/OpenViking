# Web Studio

Web Studio 是 OpenViking 的前端工作台，基于 Vite 和 React 19 构建，当前以单页应用形式运行。

它用于承接 OpenViking 的资源、会话和运维工作区，并逐步把现有能力收敛到统一的前端界面中。

## 当前状态

- 应用首页会重定向到 /resources。
- 当前顶层工作区包括 resources、sessions、operations。
- 这三个页面目前仍是占位页：
  - src/routes/resources/route.tsx
  - src/routes/sessions/route.tsx
  - src/routes/operations/route.tsx
- 对应的翻译资源在 src/i18n/locales/en.ts 和 src/i18n/locales/zh-CN.ts 中也仍然包含占位说明，用于表达当前页面骨架和后续接入方向。

产品入口与功能区规划见 [WORKSPACE_IA.md](WORKSPACE_IA.md)。

## 技术栈

- React 19
- Vite 7
- TanStack Router
- TanStack Query
- Tailwind CSS v4
- shadcn/ui
- i18next + react-i18next
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

默认端口为 3000。

## 常用命令

启动开发环境：

```bash
npm run dev
```

生产构建：

```bash
npm run build
```

预览构建产物：

```bash
npm run preview
```

运行测试：

```bash
npm run test
```

运行当前业务范围的 lint：

```bash
npm run lint
```

执行格式化并自动修复当前 lint 范围：

```bash
npm run check
```

仅检查格式：

```bash
npm run format
```

重新生成服务端客户端：

```bash
npm run gen-server-client
```

## 目录概览

核心目录如下：

- src/routes：TanStack Router 路由入口
- src/components/ui：可复用基础 UI 组件
- src/lib/ov-client：前端请求适配层
- src/gen/ov-client：OpenAPI 生成客户端
- src/i18n/locales：当前中英文翻译资源
- src/styles.css：全局样式与设计 token

当前顶层页面已迁移为目录式路由，例如：

```text
src/routes/resources/
  route.tsx
  -components/
  -hooks/
  -lib/
  -constants/
  -schemas/
  -types/
```

## 后端连接与客户端生成

前端默认连接地址为 http://127.0.0.1:1933。

这个地址同时用于：

- 运行时 API 请求
- OpenAPI 文档拉取与客户端生成

连接信息当前的存储方式：

- X-API-Key 保存在 sessionStorage，键名为 ov_console_api_key
- baseUrl、accountId、userId 保存在 localStorage

页面初始化后，这些值会同步到 src/lib/ov-client/client.ts 导出的全局 ovClient。

如果需要重新生成客户端，gen-server-client 会执行以下流程：

1. 从 http://127.0.0.1:1933/openapi.json 拉取 OpenAPI 文档。
2. 格式化中间文件。
3. 清洗 operationId。
4. 生成 src/gen/ov-client 下的最终 SDK。

生成产物目录是 src/gen/ov-client，不应手动修改。

## 请求层说明

前端请求层分为两层：

### OpenAPI 生成层

- 目录：src/gen/ov-client
- 作用：承接后端 OpenAPI 自动生成的类型和客户端

### 前端适配层

- 目录：src/lib/ov-client
- 作用：补齐前端运行时约定，例如请求头注入、telemetry 注入和错误归一化

业务代码默认应通过 src/lib/ov-client 使用接口，而不是直接依赖生成层。

## i18n 说明

当前前端已经接入 i18next，翻译资源集中在 src/i18n/locales。

现阶段需要注意两点：

- 当前 resources、sessions、operations 相关翻译中仍有占位文案。
- 当这些页面进入真实实现时，应同步改写对应翻译资源，而不是保留“后续接入”类说明。

## 文档分工

README 只负责说明项目用途、当前状态、目录入口和开发方式。

实现规范、占位页维护规则、i18n 约束和页面实现边界，统一放在 [AGENTS.md](AGENTS.md)。如果你要修改前端实现细节，先看 [AGENTS.md](AGENTS.md)。