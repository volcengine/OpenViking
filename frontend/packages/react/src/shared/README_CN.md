# 内部共享代码

[English](README.md) / 中文

供本包组件复用的内部代码，**没有公开入口**。这里的内容不出现在包根入口、子路径或 `package.json` 的 `exports` 中——宿主无法导入，也不承担兼容性承诺。

只有出现第二个真实消费者时才提升到这里；单个组件用到的东西留在该组件目录内。

内部 `components/drawer` 与 `components/modal` 是已明确确认的目录例外。Drawer 由 `FileBrowserEntries` 使用，Modal 由节点权限管理使用；其他内部代码仍遵循第二个真实消费者的门槛，详见 [Frontend Agent Guide](../../../../AGENTS.md)。

这里的样式由使用它的组件自己的样式表引入，而不是 `components/style.css`：没有公开入口指向本目录，用它的组件负责按需引入。

## 内容

- `components/button`：Arco 精简按钮，保留类型、尺寸、图标、加载态及原生事件和 ref，普通按钮默认不提交表单。组件、模块与文档共用，移除链接、按钮组和 Provider 耦合。
- `components/input`：Arco 精简输入框与文本域，保留原生输入事件、表单属性、ref、尺寸及装饰性前后缀；不包含密码显隐、计数、清空或自动尺寸。
- `components/modal`：提供过渡动画、焦点锁和共享滚动锁的 Arco 精简居中弹窗，不设 SDK 导出。内部测试使用私有构建入口，JS 入口文件不随 npm 包发布。
- `components/drawer`：提供过渡动画、焦点锁与滚动锁的右侧抽屉。通过 `children` 接收内容，由 `file-browser-entries` 提供业务内容与专属样式。
- `components/tooltip`：渲染到 `document.body` 的定位浮层，被 `file-preview` 用于复制路径提示与折叠层级菜单。它存在的理由是祖先容器的 `overflow: hidden` 会裁掉常规浮层。
- `components/popover`：白底卡片的浮层，同样 portal 到 `document.body`，被文件树的节点信息与文件预览使用。
- `components/divider`：一条分隔线，尺寸与颜色由调用方内联给出。
- `components/resizable`：可拖拽的分隔条 `ResizableHandle`，用指针捕获处理拖动、支持方向键改宽，宽度由调用方受控持有。`file-browser` 用它调整目录树宽度。

## 边界

输入框与按钮只复用 Arco 的交互逻辑。视觉规则来自火山 OpenViking 当前使用的 `@arco-design/theme-ve-o-aml-design@0.0.36`（包声明为 MIT），以该包的 `css/arco.css`、`theme.css` 和必要的 `components/Input/index.less` 覆盖为准。不是 Arco 官方默认皮肤。来源由 `vikingdb-fe` master `190844896665bc3229e03423114803d24b4faa82` 的 OpenViking 依赖及 `assets/styles/aml-design.less` 核验。

主题包默认控件为 6px 圆角、13px 字号；按钮字重 500，输入框白底带描边、聚焦时有 2px 光晕。尺寸保留 24/28/32/36px。文件树工具栏及作用域搜索等业务覆盖留在各自组件中。颜色映射位于 `src/theme.css`，宿主仍通过 `--ov-ui-color-*` 覆盖；不引入整包 CSS、全局 reset、私有图标或字体资源。

共享代码不得依赖 `components/`、`modules/` 或任何宿主契约——依赖方向是单向的，从公开组件指向本目录。需要业务数据、路由或宿主服务的能力属于 `modules/`。

文档通过 `docs/internal-controls.ts` 消费独立的私有构建产物，相关入口不随 npm 包发布。示例中的 `#demo-controls` 仅供本站使用，宿主接入时替换为自己的按钮。

开发与构建入口见 [UI workspace README](../../../../README.md)，组件约定见[组件指南](../components/AGENTS.md)。
