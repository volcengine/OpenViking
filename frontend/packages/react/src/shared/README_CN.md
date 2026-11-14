# 内部共享代码

[English](README.md) / 中文

供多个组件或区块复用的内部代码，**没有公开入口**。这里的内容不出现在包根入口、子路径或 `package.json` 的 `exports` 中：宿主无法导入，也不承担兼容性承诺。

出现第二个真实消费者时，代码才提升到这里；单个组件用到的东西留在该组件目录内。共享代码不依赖 `components/`、`modules/` 或任何宿主契约：依赖是单向的，从它们指向本目录。需要业务数据、路由或宿主服务的代码属于 `modules/`。放置规则见 [Frontend Agent Guide](../../../../AGENTS.md)。

这里的样式由使用它的组件自己的 `style.css` 引入，而不是 `components/style.css`：没有公开入口指向本目录。

## 内容

有哪些目录以实际目录为准，下面只说明每个目录的用途。

### `primitives/`

内部通用基础件。业务内容与专属样式由调用方通过 `children` 或 props 提供。

- `button`：Arco 精简按钮，提供类型、尺寸、图标、加载态、原生事件和 ref。只有 `htmlType="submit"` 时才提交表单。不含链接、按钮组和 `ConfigProvider` 耦合。
- `input`：Arco 精简 `Input`，提供原生事件、表单属性、ref、尺寸以及装饰性前后缀，另有带样式的 `TextArea`。不含密码显隐、计数、清空和自动尺寸。
- `select`：通过 children 组合选项，单选和多选统一使用数组值，提供 Input 外壳、无边框 Simple 模式及自定义回显和触发器。见 [Select](./primitives/select/README.md)。
- `modal`：改编自 Arco 的受控居中弹窗，提供过渡动画、焦点锁和共享的 body 滚动锁。关闭按钮文案放在它自己的 `locales/` 中。
- `drawer`：右侧抽屉，提供过渡动画、焦点锁和共享的 body 滚动锁，内容通过 `children` 传入。
- `tooltip`：渲染到 `document.body` 的定位浮层，悬停或聚焦时显示，也可以点击切换。渲染到 body 是因为祖先容器的 `overflow: hidden` 会裁掉常规浮层。
- `popover`：`tooltip` 的白底卡片版本，用于结构化或可交互的内容，同样渲染到 `document.body`，触发方式与方位选项和 `tooltip` 一致。
- `divider`：一条 `role="separator"` 的分隔线，长度、粗细和颜色由调用方给出。
- `resizable`：`ResizableHandle`，可拖拽的 `role="separator"` 分隔条，用指针拖动或方向键调整相邻面板的宽度。宽度由调用方持有，每个夹取后的值通过 `onResize` 回传。
- `overlay`：`tooltip`、`popover`、`modal`、`drawer` 这类浮层共用的定位与滚动工具。`useOverlayPosition` 在空间不足时把浮层翻到对侧并夹在视口内，`lockBodyScroll` 记录各个持有者，最后一个释放时恢复宿主的内联样式，`OverlayPlacement` 是共用的方位类型。
- `notification`：右上角通知栈，展示任务状态与结果反馈，见 [Notification](#notification)。

### `auth/`

`components/auth/` 下六个表单共用的部分：带标题、说明、错误提示、提交按钮和协议确认的 `<form>` 外框（`form-frame.tsx`），带可选密码显隐的输入项（`fields.tsx`），协议链接与确认弹窗（`agreements.tsx`），管理取值与校验的 `useForm`，提供本地化文案的 `useAuthLabels`，以及 `auth.css`。

### `dashboard/`

`DashboardState`（加载中、出错并带重试按钮、为空三种状态块），以及 `dashboard.css`，其中是 WikiDashboard 与 ExperienceDashboard 共用的状态块、页签栏和主体样式。

### 语言

- `locale.ts`：`Locale` 类型（`'en-US' | 'zh-CN'`）、`resolveLocale` 与 `mergeLabels`。显式的 `labels` 项优先于语言的默认值，`undefined` 继承默认值，空字符串保留为显式覆盖。文件里没有业务文案。
- `locale-context.tsx`：`useLocale` 与 `LocaleProvider`，各公开入口共用的语言作用域。它与请求 Client 的 Context 分离，因此切换语言不影响请求和状态。

译文放在各自所属目录的 `locales/en-US.ts` 与 `locales/zh-CN.ts`；`auth/` 和 `primitives/modal/` 采用同样的布局。

### Notification

- `useNotification` 返回实例隔离的操作 API 与上下文占位，占位放在调用方的语言与主题作用域内。
- API 提供 `loading`、`info`、`success`、`warning`、`error`、`normal`、`remove` 和 `clear`，同一 `id` 原位更新通知，`btn` 提供操作内容。
- `loading` 使用与 Button 共用的内置 `LoadingIcon`，默认持续显示；其他通知默认 3000 ms 后关闭，`duration: 0` 禁用自动关闭，悬停和键盘焦点暂停计时。
- 关闭按钮与自动关闭计时器在开始移除前调用 `onDismiss`；移除与清空在退出动画结束后调用各通知的 `onClose`，任务轮询、取消任务和终态操作由调用方负责。
- 仅提供右上角，不提供全局 API。
- [内部任务演示](../../../../docs/docs/_demos/notification-tasks.tsx) 消费私有文档构建，手动模拟运行、成功和失败状态。

## 说明

- Button 与 Input 只复用 Arco 的交互逻辑。视觉规则改编自 `@arco-design/theme-ve-o-aml-design@0.0.36`（MIT），不使用 Arco 默认皮肤，颜色映射到 `src/theme.css` 的 `--ov-ui-color-*`，宿主可以覆盖。不引入全局 reset、私有图标或字体资源。Arco 的声明见 [`LICENSE.arco-design`](../../LICENSE.arco-design)。
- 文档站通过 `docs/internal-controls.ts` 使用内部控件的构建产物（`rslib.config.ts` 中的 `docs-controls` 入口），这些文档专用产物不进入 npm 包。示例里的 `#demo-controls` 仅供文档站使用，宿主接入时换成自己的控件。
- 测试通过 `scripts/test.mjs` 里的私有构建访问内部基础件，不经过包导出。

开发命令见[前端工作区 README](../../../../README.md)，组件约定见[组件指南](../components/AGENTS.md)。
