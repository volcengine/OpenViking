# FileTree

面向资源浏览的目录树。宿主把树状态展平成有序行列表传进来，通过 `operations` 提供异步执行能力；组件负责渲染、键盘交互，并在操作成功后通知。

```tsx
import { FileTree } from '@openviking/react/components/file-tree';
import '@openviking/react/components/file-tree/style.css';

<FileTree
  rows={rows}
  selectedId={selectedId}
  operations={{
    select: async (row) => setSelectedId(row.id),
    toggleExpand: async (row) => toggle(row.id),
  }}
/>;
```

`rows` 从哪来、分页怎么取、权限怎么判，都由宿主决定——组件不碰这些。

## 为什么是「展平行列表」

目录树最常见的做法是让组件接收嵌套结构，自己递归渲染、自己维护展开集合、自己决定何时请求子节点。这样组件就得知道宿主的分页策略、错误重试方式、账号作用域切换时机，最后变成一个什么都懂一点、又和某个后端绑死的东西。

这里反过来：**宿主负责把树状态压平成一维数组，组件只按顺序渲染**。

```ts
type FileTreeRow =
  | FileTreeEntryRow // 目录或文件
  | FileTreeLoadingRow // 子节点加载中
  | FileTreeLoadMoreRow // 还有下一页
  | FileTreeErrorRow // 子节点加载失败
  | FileTreeEmptyRow; // 展开后是空的
```

带来的直接结果：

- 分页、懒加载、失败重试都由宿主在自己那侧实现，组件不需要知道「怎么取下一页」。
- 只返回全部子节点的数据源不会产出 `load-more` 行，行为自然等价，不用传开关。
- 行是普通对象，宿主可以在传进来之前做任何加工（排序、过滤、权限收敛）。

代价是宿主需要写一个「树状态 → 行数组」的转换函数。这份成本换的是组件可以同时服务分页数据源与全量数据源、服务不同的鉴权模型，而 UI 完全一致。

## 组件负责什么

- 渲染行：图标、缩进、层级标记、副标题、写入中指示。
- 键盘交互：方向键移动、左右键展开折叠、Enter/Space 选中，`role="tree"` 与 `aria-*` 完整。
- 选中、展开、加载更多、重试和打开保留策略设置通过 `operations` 执行，完成后才触发对应的 `on…` 成功通知；失败时不触发。省略执行方法时禁用对应操作，省略通知不影响执行。
- 三类整树级状态的展示位：加载中、加载失败、空树，内容由宿主给。
- 悬停浮层的延迟、定位与抑制。

## 组件不负责什么

- 不请求任何数据，不建连接，不做权限判断。
- 不维护展开集合与选中项——两者都是受控的。
- 点击目录与点击文件共用 `operations.select`，由宿主按 `row.type` 分流；选中成功后通过 `onSelect` 通知。
- 工具栏、搜索、导入来源、权限流程属于页面层，通过 `slots.toolbar` 注入。

## 扩展点

**行尾与悬停**：`renderDashboard` 放行尾指示器（例如看板入口），`renderRowTooltip` 放悬停内容。两者返回 `null` 即不显示；看板判定属于产品语义，组件不内置路径规则。

**行级一次性效果**：高亮、闪烁、临时描边不属于树的属性。组件给每条行元素设置 `data-file-tree-row-id`（常量 `FILE_TREE_ROW_ATTRIBUTE`），页面层可直接查询该元素，也可以用 `getRowAttributes` 按行数据追加属性，或用 `ref` 句柄的 `getRowElement` / `scrollToRow`。三种方式可组合，都不改变组件状态。

**ref**：按 `forwardRef` 契约定义（类型 `FileTreeComponent`），React 18 与 19 都能拿到句柄。不要把它声明成 `FileTreeProps` 的字段——React 18 会把 `ref` 从 props 中剥离。

## 主题

样式局部化，不修改应用的全局样式。颜色、圆角、焦点环通过 `--ov-ui-*` 变量覆盖，完整清单见组件文档的主题变量表。

## 文档与开发

- 属性表、可运行示例、主题变量见[组件文档](../../../../../docs/docs/en/components/file-tree.mdx)（[中文](../../../../../docs/docs/zh/components/file-tree.mdx)）。
- 开发与构建命令见[前端工作区 README](../../../../../README.md)，模块约定见[包说明](../../../README.md)。
- 独立样式入口 `@openviking/react/components/file-tree/style.css`，与 JS 入口分离，可按需引入。
