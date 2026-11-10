# 内部共享代码

[English](README.md) / 中文

供本包组件复用的内部代码，**没有公开入口**。这里的内容不出现在包根入口、子路径或 `package.json` 的 `exports` 中——宿主无法导入，也不承担兼容性承诺。

只有出现第二个真实消费者时才提升到这里；单个组件用到的东西留在该组件目录内。

内部 `components/drawer` 是已明确确认的目录例外：它作为通用右侧抽屉保留在这里，目前唯一直接消费者是 `FileBrowserEntries`。其他内部代码仍遵循第二个真实消费者的门槛，详见 [Frontend Agent Guide](../../../../AGENTS.md)。

这里的样式由使用它的组件自己的样式表引入，而不是 `components/style.css`：没有公开入口指向本目录，用它的组件负责按需引入。

## 内容

- `components/drawer`：提供过渡动画、焦点锁与滚动锁的右侧抽屉。通过 `children` 接收内容，由 `file-browser-entries` 提供业务内容与专属样式。
- `components/tooltip`：渲染到 `document.body` 的定位浮层，被 `file-preview` 用于复制路径提示与折叠层级菜单。它存在的理由是祖先容器的 `overflow: hidden` 会裁掉常规浮层。
- `components/popover`：白底卡片的浮层，同样 portal 到 `document.body`，被文件树的节点信息与文件预览使用。
- `components/divider`：一条分隔线，尺寸与颜色由调用方内联给出。
- `components/resizable`：可拖拽的分隔条 `ResizableHandle`，用指针捕获处理拖动、支持方向键改宽，宽度由调用方受控持有。`file-browser` 用它调整目录树宽度。

## 边界

共享代码不得依赖 `components/`、`modules/` 或任何宿主契约——依赖方向是单向的，从公开组件指向本目录。需要业务数据、路由或宿主服务的能力属于 `modules/`。

开发与构建入口见 [UI workspace README](../../../../README.md)，组件约定见[组件指南](../components/AGENTS.md)。
