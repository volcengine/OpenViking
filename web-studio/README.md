# Web Studio

这是 OpenViking 的前端工作区，基于 TanStack Start 构建，当前主要使用以下技术栈：

- React 19
- TanStack Router 文件路由
- Vite
- Tailwind CSS v4
- shadcn/ui
- lucide-react

## 启动与构建

安装依赖：

```bash
npm install
```

启动开发环境：

```bash
npm run dev
```

默认开发端口为 `3000`。

生产构建：

```bash
npm run build
```

本地预览构建结果：

```bash
npm run preview
```

代码检查：

```bash
npm run lint
npm run format
npm run check
```

测试：

```bash
npm run test
```

## Client Codegen

当前前端请求客户端不是手写维护，而是基于 OpenAPI 自动生成。

生成命令：

```bash
npm run gen-server-client
```

这条命令会串联执行以下步骤：

1. 从 `http://127.0.0.1:1933/openapi.json` 拉取最新 OpenAPI 文档
2. 使用 `openapi-format` 输出格式化后的中间文件
3. 运行 `script/gen-server-client/polishOpId.js` 对 `operationId` 做二次整理
4. 使用 `openapi-ts` 生成最终客户端代码

相关文件位置：

- `script/gen-server-client/gen-server-client.sh`：codegen 总入口脚本
- `script/gen-server-client/oaf-generate-conf.json`：`openapi-format` 配置
- `script/gen-server-client/polishOpId.js`：`operationId` 后处理脚本
- `script/gen-server-client/generate/openapi-formatted.json`：格式化后的中间 OpenAPI 文件
- `src/gen/ov-client`：最终生成的前端客户端代码

`polishOpId.js` 的职责是把 `<pathRef>` 风格的原始 `operationId` 转成更适合前端使用的 camelCase 方法名。当前规则包括：

- 忽略 `api/v1` 这类版本前缀
- 中间 path parameter 会优先内联到前一个相似资源段中
- 末尾 path parameter 会整理为 `By...` / `And...` 后缀

例如：

- `/api/v1/sessions/{session_id}/context` -> `getSessionIdContext`
- `/api/v1/sessions/{session_id}/archives/{archive_id}` -> `getSessionIdArchiveByArchiveId`

使用和维护时注意：

- 运行 codegen 前，需要本地后端能提供 `http://127.0.0.1:1933/openapi.json`
- 不要手动修改 `src/gen/ov-client` 内的生成产物，应该通过重新执行 `npm run gen-server-client` 更新
- 如果后端新增或调整了路由，优先检查生成后的 `operationId` 是否仍然符合预期
- 如果需要修改命名规则，调整 `script/gen-server-client/polishOpId.js`，然后重新执行生成命令验证结果

## ov-client 适配层

前端真正使用的不是 `src/gen/ov-client` 里的原始生成代码，而是 `src/lib/ov-client` 这一层薄适配。

职责拆分如下：

- `src/gen/ov-client`：基于 OpenAPI 自动生成的原始 SDK，只描述真后端接口，不承载前端运行时约定
- `src/lib/ov-client`：在生成 SDK 之上补齐前端侧约定，例如连接信息注入、telemetry 默认行为、错误格式归一化

相关文件：

- `src/lib/ov-client/client.ts`：创建和维护适配后的 client
- `src/lib/ov-client/errors.ts`：统一错误对象和结果解包
- `src/lib/ov-client/types.ts`：适配层类型定义
- `src/lib/ov-client/index.ts`：统一导出入口

### 对接对象

当前 `src/lib/ov-client` 直接对接真 OpenViking HTTP Server，而不是旧 console 的 BFF。

也就是说：

- 请求目标是 OpenAPI 中定义的真实后端路径，例如 `/api/v1/...`
- 不再依赖旧控制台里的 `/console/api/v1` 前缀
- 不再依赖 BFF 提供的 `/ov/...` 别名
- 不再依赖 BFF 的 `runtime/capabilities` 语义

当前默认对接地址是 `http://127.0.0.1:1933`。这也是本地 codegen 使用的后端地址。

如需调整目标服务地址，有两种方式：

1. 创建 client 时传入 `baseUrl`
2. 运行时调用 `ovClient.setOptions({ baseUrl })`

### 适配层做了什么

适配层当前只做三类事情：

1. 直连真后端所需的连接信息注入

- 自动注入 `X-API-Key`
- 自动注入 `X-OpenViking-Account`
- 自动注入 `X-OpenViking-User`
- 自动注入 `X-OpenViking-Agent`
- API key 默认沿用旧前端的会话存储键 `ov_console_api_key`

2. 对齐旧 console/BFF 的 telemetry 默认行为

适配层会在以下 POST 请求中自动补 `telemetry: true`，前提是请求体是普通对象且调用方没有显式传 telemetry：

- `/api/v1/search/find`
- `/api/v1/resources`
- `/api/v1/sessions/{session_id}/commit`

这个行为是为了与旧 BFF 保持一致，避免页面迁移后行为悄悄变化。

3. 优化错误返回格式

适配层会把以下几类异常统一归一化为 `OvClientError`：

- 后端标准 JSON 错误包 `{ status: "error", error: ... }`
- 非 JSON 的 HTTP 错误文本
- 网络错误和 Axios 错误

统一后的错误字段包括：

- `code`
- `message`
- `statusCode`
- `requestId`
- `details`
- `responseBody`

其中，若后端返回 `UNAUTHENTICATED` 且消息中包含 `Missing API Key`，适配层会补上旧前端一致的提示：`Please go to Settings and set X-API-Key.`

### 适配层不做什么

当前适配层明确不处理以下内容：

- 不实现 BFF 的 `runtime/capabilities`
- 不在前端预判写权限或模块权限
- 不改写 OpenAPI 路径或参数结构
- 不修改 `src/gen/ov-client` 的生成产物

权限和可写性以真后端返回结果为准，页面层按实际错误做反馈。

### 调用约定

页面或业务模块统一从 `src/lib/ov-client` 导入，而不是直接从 `src/gen/ov-client` 导入。

推荐调用方式：

1. 先配置连接信息
2. 调用生成 SDK 方法，并显式传入 `client: ovClient.client`
3. 使用 `getOvResult()` 解包 `result`
4. 在页面层捕获 `OvClientError`

示例：

```ts
import {
	getOvResult,
	getSystemStatus,
	ovClient,
	OvClientError,
} from '#/lib/ov-client'

ovClient.setConnection({
	apiKey: '<your-api-key>',
	accountId: 'default',
	userId: 'default',
	agentId: 'default',
})

try {
	const result = await getOvResult(
		getSystemStatus({
			client: ovClient.client,
		}),
	)

	console.log(result)
} catch (error) {
	if (error instanceof OvClientError) {
		console.error(error.code, error.message)
	}
}
```

如果只需要调整目标服务地址，可以这样做：

```ts
import { ovClient } from '#/lib/ov-client'

ovClient.setOptions({
	baseUrl: 'http://127.0.0.1:1933',
	defaultTelemetry: true,
})
```

### 使用约定

为了避免调用方式分裂，后续开发建议遵守这些约定：

- 业务代码默认只从 `#/lib/ov-client` 导入
- 不直接改 `src/gen/ov-client`
- 需要拿 `result` 时，优先使用 `getOvResult()`
- 需要保留完整响应头或状态码时，再直接处理原始 SDK 返回值
- 页面层统一消费 `OvClientError`，不要自行拼接错误文案
- 若某个请求不希望默认补 telemetry，显式传 `telemetry: false`

## 项目结构

核心目录如下：

```text
web-studio/
├── src/
│   ├── components/
│   │   ├── ui/          # shadcn/ui 生成的基础组件
│   │   └── ...          # 业务级共享组件
│   ├── lib/             # 工具函数与通用逻辑
│   ├── routes/          # TanStack Router 文件路由
│   ├── main.tsx         # 前端入口
│   ├── router.tsx       # Router 初始化
│   ├── routeTree.gen.ts # 自动生成，禁止手改
│   └── styles.css       # 全局样式与主题变量
├── components.json      # shadcn/ui 配置
├── package.json
└── README.md
```

## 开发约定

### 路由

项目使用 TanStack Router 的文件路由，所有页面都放在 `src/routes` 下。

常见映射关系：

1. `src/routes/index.tsx` 对应 `/`
2. `src/routes/about.tsx` 对应 `/about`
3. `src/routes/settings/profile.tsx` 对应 `/settings/profile`
4. `src/routes/blog/$slug.tsx` 对应动态路由 `/blog/:slug`
5. `src/routes/__root.tsx` 用于全局路由壳和公共布局

新增页面时，直接在 `src/routes` 中创建文件即可。例如新增 `/settings` 页面：

```tsx
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/settings')({
	component: SettingsPage,
})

function SettingsPage() {
	return <main className="page-wrap px-4 py-10">Settings</main>
}
```

注意事项：

- `src/routeTree.gen.ts` 由路由插件自动生成，不要手动修改。
- 页面级布局优先通过路由结构表达，跨页面公共壳放在 `src/routes/__root.tsx`。

### 组件

组件分两层：

1. `src/components/ui`：shadcn/ui 生成的基础组件，尽量保持通用。
2. `src/components`：项目自己的业务组件、页面区块、组合组件。

建议做法：

- 基础按钮、输入框、对话框等放在 `src/components/ui`
- 页面头部、空状态、工具栏、卡片区块等放在 `src/components`

### 添加 shadcn/ui 组件

当前项目已配置好 `components.json`，并启用了别名：

- `#/components`
- `#/components/ui`
- `#/lib`

新增 shadcn/ui 组件时，在 `web-studio` 目录执行：

```bash
npx shadcn@latest add card
```

例如添加 button、card、dialog：

```bash
npx shadcn@latest add button card dialog
```

生成后的组件默认位于 `src/components/ui`。

### 使用 lucide 图标

项目图标库使用 `lucide-react`。直接按需导入：

```tsx
import { Plus, Settings } from 'lucide-react'
import { Button } from '#/components/ui/button'

export function Toolbar() {
	return (
		<div className="flex gap-2">
			<Button>
				<Plus className="size-4" />
				新建
			</Button>
			<Button variant="outline">
				<Settings className="size-4" />
				设置
			</Button>
		</div>
	)
}
```

### 样式

样式主要由以下两部分组成：

1. `src/styles.css`：全局样式、主题变量、基础 token
2. 组件内 Tailwind class：页面和组件局部样式

如果要调整整体视觉风格，优先从 `src/styles.css` 入手；如果只是局部页面样式，直接修改对应组件即可。

## 推荐开发流程

新增一个页面或功能时，通常按这个顺序：

1. 在 `src/routes` 中创建或调整路由文件
2. 在 `src/components` 中拆出页面区块或复用组件
3. 需要基础 UI 时，用 shadcn CLI 添加到 `src/components/ui`
4. 需要图标时，从 `lucide-react` 导入
5. 完成后运行 `npm run check`

## 维护说明

- 不要手动修改 `src/routeTree.gen.ts`
- 新增组件时优先复用已有 `ui` 组件，避免重复造轮子
- 路由文件保持轻量，复杂 UI 尽量下沉到 `src/components`
- 项目已配置路径别名，优先使用 `#/...` 形式导入
