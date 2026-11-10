# ScopeSwitcher

[English](README.md) / 中文

`ScopeSwitcher` 是已实现的无状态面包屑布局。通过 `@openviking/react/components/scope-switcher` 导出的独立 `ScopeSelector` 子组件组合层级，不再支持旧的 `levels` 属性。每个子组件维护自己的展开状态、接收本级数据，布局根组件不汇总状态或订阅请求。

```tsx
<ScopeSwitcher>
  <ScopeSelector kind="selectable" identity={...} data={...} state={...} actions={...} />
  <ScopeSelector kind="selectable" identity={...} data={...} state={...} actions={...} />
  <ScopeSelector kind="fixed" identity={...} trigger={{ label: 'default' }} />
</ScopeSwitcher>
```

## 职责边界

- 宿主决定子组件的数量、顺序与语义。不需要的层级直接省略；`fixed` 层级只展示当前值，不提供下拉。
- 选中项、选项、搜索关键字、加载、错误和分页由宿主提供。`data.options` 是判断选中值是否有效的权威集合，`data.visibleOptions` 只是渲染子集，因此搜索过滤不会让当前作用域失效。组件不取数、不持久化、不接管路由；`actions.onScroll` 上报滚动信息，供宿主实现分页。
- 库 → 空间 → 用户的接口级联可使用 `@openviking/react/modules/scope-cascade` 的 `useScopeCascade` 和 `ScopeCascade`。模块负责请求协调与独立订阅，组件仍为纯视图。

## 展开与选择

- 不传 `state.visible` 时，各选择器自行管理开合。可选的 `actions.onVisibleChange` 上报开合请求，包括选择、Escape、外部点击和焦点离开选择器。
- 传入 `state.visible` 后由宿主控制开合，并通过 `actions.onVisibleChange` 写回状态。选择时先请求关闭，再调用 `actions.onSelect`；受控宿主可以等异步操作完成后再关闭。
- 选择后，面板实际关闭时焦点返回触发按钮；如果焦点已移到外部则不抢回。Escape 关闭并恢复触发按钮焦点。禁用的选择器不能展开。

## 搜索、插槽与键盘交互

- 传入 `actions.onSearchKeywordChange` 启用搜索。当已加载数量或 `total` 超过 `SCOPE_SWITCHER_SEARCH_THRESHOLD`（10），或 `searchKeyword` 非空时显示搜索框。宿主过滤选项，视图高亮命中内容。
- `slots.renderPanel({ search, list })` 可将内置搜索框、列表或状态节点与标题、动作和自定义控件组合。自定义控件保留自身的键盘行为。面板高度上限不变，宿主内容更高时在面板内滚动。
- `slots.renderOption(content, option)` 可扩展或替换默认行内容。嵌套交互控件不会触发行选择。禁用选项以及 `busy` 时的全部选项都不可选择。
- 选项行支持 ArrowUp/ArrowDown、Home/End 移动焦点，Enter/空格选择。内置搜索框的上下方向键进入列表，Home/End 保留文本编辑行为。
- 触发器使用 `aria-haspopup="dialog"` 与 `aria-expanded`；面板内的 `listbox` 包含带 `aria-selected` 的 `option` 行。加载、空态与错误支持文案和兜底节点。

## 样式与开发

显式导入 `@openviking/react/components/scope-switcher/style.css`，包含面包屑胶囊、展开和选中态、下拉面板，以及窄屏底部面板样式。JavaScript 不隐式引入 CSS。

整行 32px 高，无描边。每一段是独立的 32px 高、16px 圆角胶囊，左右 8px 内边距、图标与文字间距 8px，文字 13px、字重 500。悬停、聚焦和展开时描主色边框；常态保留透明描边，因此边框出现时不会引起位移。

整行不换行。空间不足时各层级收缩、标签省略，因此文案在中英文之间切换不会改变外框高度。

带下拉的层级在悬停、聚焦和展开时给按钮铺浅底，与面板里选项行的反馈一致。不带下拉的层级（`kind: 'fixed'`）几何参数完全相同，只去掉箭头和悬停反馈，因此只读的作用域仍属于同一条面包屑。相邻层级由 CSS 画出 `#c9cbd4` 的 `/`。

面板内悬停行铺浅底，选中行常驻主色浅底、文字加粗、行尾显示勾选；文字颜色不变，设计稿用字重和底色区分选中。搜索命中片段染主色，不加黄底。

选项行首图标装在一个 24px 的底槽里：纯白底、0.5px `#edeef2` 描边、12px 圆角，图形本身保持 14px。宿主渲染圆形头像时把 `--ov-ui-scope-switcher-option-icon-radius` 设为 `50%`。

主题变量声明在 `.ov-scope-switcher` 上，宿主可只覆盖其中一条：`--ov-ui-scope-switcher-divider`、`-icon`、`-option-icon`、`-option-icon-size`、`-option-icon-radius`、`-row-hover`、`-tag-background`、`-tag-text`、`-panel-width`、`-option-height`、`-option-gap`、`-list-max-height`、`-panel-max-height`。

共用颜色走包级主题基线，写法为 `var(--ov-ui-color-<token>, var(--ov-ui-<token>-default, 兜底))`：宿主在祖先上设置了 `--ov-ui-color-*` 就用宿主的，否则用基线默认值。组件不在自己根节点上声明公开的 `--ov-ui-color-*`，否则会阻断宿主的值向下继承。这里用到的 token：`text-1`、`text-3`、`text-disabled`、`border-1`、`border-2`、`border-3`、`bg-1`、`primary`、`primary-light`。

开发命令见[前端工作区 README](../../../../../README.md)，模块约定见[包说明](../../../README.md)。完整字段契约以类型声明为准。
