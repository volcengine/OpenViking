# Web Studio

Web Studio 是 OpenViking 前端工作台，一个基于 Vite 的 React 单页应用。

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

## UI 与样式约定

- 基础组件放在 src/components/ui
- 业务页面组件放在 src/components/legacy 或其他业务目录
- 全局设计 token 和基础样式集中在 src/styles.css
- 已配置路径别名 #/...，新增代码时优先沿用

如果需要新增 shadcn/ui 组件，在当前目录执行：

```bash
npx shadcn@latest add button card dialog
```

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