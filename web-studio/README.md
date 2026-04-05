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
