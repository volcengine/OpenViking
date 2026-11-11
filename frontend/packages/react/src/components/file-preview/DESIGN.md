# FilePreview 实现设计（工作文档）

对照 `vikingdb-fe` 火山版实现与 `@ark-design/ui/file-viewer` 能力，梳理已定 API 的落地逻辑与缺口。
本文件是实现前的工作稿，落地后删除，不随包发布。

## 一、结论摘要

对照基准是 `vikingdb-fe` 的 `codex/feat/team-compile-frontend-20261024`（`6c13b01bd`），它把 OpenViking 控制台所有能接的预览都接进了 `FileViewerCore`。

1. **必须用 `FileViewerCore` + 自带 adapters，不能用一体化 `FileViewer`。** 一体化组件在 beta.5 里把内置 adapters 写死（`createBuiltinAdapters`），不接受配置，Markdown 图片的 `viking://` → blob 鉴权加载就无处挂载。
2. **`toolbar={false}` 是既定做法，分支里 6 处 `FileViewerCore` 全部关掉了。** 外壳（页签行、标题行、视图切换、操作）全部自绘。
3. **视图模式用底座的受控 `viewMode`，不用 ref。** `@ark-design/ui@0.0.0-beta.6` 已发布并已升级，`viewMode` / `onViewModeChange` 可用。见 5.4。
4. **文案不改底座**，`locale` 透传 + `slots` 接管兜底态，与分支一致。见 5.3。
5. **`onOpenParent(path)` 语义要改。** 火山版面包屑每一级都可点，传的是被点那段的祖先 URI。见 5.1。
6. **缺受控 `activeKey`。** file-browser 的当前资源由宿主/路由驱动，只有 ref 命令式无法满足。
7. **`source` 没有截断标记**，宿主传截断内容时组件无法区分「文件就这么短」和「只读了一部分」。

**剩余外部依赖**（见六）：图标分支合并。`@ark-design/ui` 受控 `viewMode` 已随 `beta.6` 就位。

## 一之二、火山版的真实构成（对照基准）

**以 `codex/feat/team-compile-frontend-20261024` 为准**，分支 `6c13b01bd`。该分支当时用 `@ark-design/ui@0.0.0-beta.5`，本仓库现已升级到 `beta.6`（多了受控 `viewMode`）。

| 组件                                            | 职责                                                                   |
| ----------------------------------------------- | ---------------------------------------------------------------------- |
| `ContextFileViewerPreview.tsx`                  | 统一入口：12 种 kind → adapter 映射、source 构造、slots、viewMode      |
| `ContextFilePreview.tsx`                        | 上层分派：需要原文件时先下载；不满足条件时降级到 `DetailEditor` / 兜底 |
| `contextMarkdownAdapter.tsx`                    | 包原生 markdown adapter：鉴权图片、viking 链接、frontmatter、wiki 图谱 |
| `MarkdownFileViewer/index.tsx`                  | 只读 markdown 的独立封装（版本历史复用）                               |
| `CompatibleFilePreview.tsx`                     | 用自定义 adapter 把任意 ReactNode 塞进 Core 的布局体系                 |
| `UnsupportedFilePreview.tsx`                    | `adapters=[]` + `downloadFallback` 槽，统一「不支持预览」态            |
| `CodeFilePreview.tsx` / `VersionDiffViewer.tsx` | 纯 code / diff 场景                                                    |
| `DetailEditor`                                  | **仍在用**：图片、视频、编辑、以及 FileViewer 覆盖不到时的降级路径     |

关键事实：**图片和视频没有交给 FileViewer**（`isSupportedImagePreviewFile` / `isSupportedVideoPreviewFile` 仍走 `DetailEditor`），`PREVIEW_EXTENSIONS` 里把它们标成 `legacy-image` / `legacy-video` 显式排除。

所以对齐策略：**交互与信息层级看 `ContextDetailPanel`，adapter 与 source 的用法看 `ContextFileViewerPreview`**。

## 二、已定 API → FileViewer 映射

| FilePreview                 | 落到 FileViewer                        | 说明                                                                                   |
| --------------------------- | -------------------------------------- | -------------------------------------------------------------------------------------- |
| `source.content`            | `FileSource{kind:'bytes'}`             | 用 `TextEncoder` 转 `Uint8Array`，不用 `new File()`，避免依赖 File 全局（SSR）         |
| `source.file`               | `FileSource{kind:'file'}`              | 直接透传                                                                               |
| `source.url`                | `FileSource{kind:'url'}`               | `mode` 用默认 `content`；大文件可让 Adapter 走 `reference`                             |
| `source.fileName`           | `FileSource.name`                      | 格式识别与标题都靠它                                                                   |
| `source.fileType`           | `FileSource.fileType`                  | 优先级高于扩展名                                                                       |
| `source.path`               | 组件自用                               | 面包屑与复制路径，不进底座                                                             |
| `resources`                 | `FileViewerCoreProps.resources`        | 类型同源                                                                               |
| `operations.download`       | `capabilities.download` + `onDownload` | 见 4.2                                                                                 |
| `slots.loadingFallback`     | `slots.loading`                        | 形态不同：底座是 `ComponentType<LoadingSlotProps>`，我们的是 `ReactNode`，需要包一层   |
| `slots.errorFallback`       | `slots.error`                          | 同上                                                                                   |
| `slots.unsupportedFallback` | `unsupportedFallback`                  | 直接是 `ReactNode`                                                                     |
| `labels`                    | 组件外壳文案 + `locale`                | 关掉工具栏后剩余的内置文案只有状态态（加载 / 确认 / 下载兜底），随 `locale` 走，见 5.3 |

## 三、组件内部逻辑

### 3.1 结构

```
FilePreview (root, .ov-file-preview)
├── tab row            role=tablist，组件渲染
├── content header     面包屑 / 标题 / 视图切换 / 复制 / 操作 / 插槽
└── body
    ├── content tab → 宿主节点，原样渲染
    └── file tab    → FileViewerCore
```

外壳全部由组件渲染，底座只负责正文，`toolbar={false}` 固定写死（与火山版一致），不对外暴露。理由：底座的工具栏自带下载、全屏、页码、搜索、缩放和视图切换，与我们的标题行是两套入口，同时出现会重复且样式割裂。

关闭工具栏的连带影响：

- `capabilities`（download / fullscreen / page / search / zoom）全部失效，下载改由 `operations.download` 触发，其余能力不提供。
- `ViewerHandle` 的 page / search / zoom 没有 UI 入口，只能通过 ref 暴露给宿主（见 5.11）。
- `viewMode` 只剩 `getDefaultViewMode` 一个决定因素（见 5.4）。

### 3.2 文件页签的渲染分支（自上而下短路）

1. `source` 无任何可用值 → `unsupportedFallback` / 内置 `binaryUnsupported`
2. `content === ''`（严格空串）→ 内置「文件为空」空态
3. `source.truncated` → 正文渲染后追加截断提示（见 5.5）
4. 其余 → `FileViewerCore`

「空白内容」（`' \n'`）不是空态，交给底座渲染成空白的文本视图。这条与火山版一致，是修过的 bug，不能退回 `trim()` 判断。

### 3.3 页签状态

- 组件持有 `activeKey`（受控优先，见 5.2）。
- 每个页签的 `viewMode` 由组件持有，通过包一层 `getDefaultViewMode` 传给底座（见 5.4）。
- 编辑中禁止切页签：`editing.saving` 为真时所有页签按钮 `disabled`，与火山版一致。
- `disabled` 的页签点击无效，`setActiveTab` 返回 `false`。

### 3.4 编辑

沿用已删除的 `use-file-preview-editing` 逻辑，改两点：

- 进入编辑时把 `viewMode` 切到 `source`（组件自己的状态，见 5.4），编辑区渲染 `<textarea>` 而非底座，编辑完成后回到 `preview`。这样不依赖底座视图切换，也省掉 Monaco 的体积。
- 保存期间 `pendingSave` 拦住重复提交，失败保留草稿并显示 `labels.saveFailed`。

### 3.5 操作与通知

沿用 file-tree 的 `use-file-tree-actions` 模式：`operations.x` 返回的 Promise 成功后才触发 `onX`，失败静默；同一操作在飞时忽略重复调用。

复制由组件完成（`navigator.clipboard.writeText`），成功后通知。复制失败不通知。

## 四、与火山版逐项对照

| 火山版能力                       | 开源版落点                                                     | 结论                                       |
| -------------------------------- | -------------------------------------------------------------- | ------------------------------------------ |
| 页签行（文件 / 经验看板）        | `tabs`                                                         | 直接对应                                   |
| 内容区标题行、面包屑             | 组件 header                                                    | 逐段可点，只改注释，见 5.1                 |
| 预览 / 源码切换                  | 组件 header + 底座受控 `viewMode`                              | 见 5.4                                     |
| Markdown 渲染 + viking 图片      | 包原生 markdown adapter，用 `resources` 的 `getObjectURL` 注入 | 分支做法，比 `processSrcBeforeLoad` 更可控 |
| JSON 格式化                      | `json({ defaultExpandDepth })`                                 | 底座内置                                   |
| JSON > 2 MiB 降级                | 宿主判大小后改走 content tab 或纯文本                          | 组件不判大小                               |
| 会话 `messages.jsonl` 渲染成对话 | content tab                                                    | 必须走 content tab，见 5.6                 |
| HTML 预览                        | `html()` adapter                                               | 底座内置，分支已接                         |
| CSV / TSV / XLSX                 | `table()` adapter                                              | 分支对 xlsx 要求先下载原文件               |
| PDF / Word / PPT                 | `pdf` / `word` / `powerpoint`，`initialFit: 'fit-width'`       | 分支参数，直接沿用                         |
| 音频                             | `media()`，`source.mode = 'reference'`                         | 分支做法：引用式，避免整包读入内存         |
| CAD / 3D                         | `cad()` / `model3d()`                                          | 3D 的 `resources` 要按相对名传入，见 5.16  |
| 图片渲染                         | `image()` adapter                                              | 已接入，见 5.7                             |
| 视频 / 音频渲染                  | `media()` adapter                                              | 已接入，见 5.7                             |
| 图片相邻切换 + 预加载            | 组件不管                                                       | 依赖兄弟文件列表，归 file-browser，见 5.7  |
| 视频抽帧 + 转录                  | 组件不管                                                       | 业务增强，不进公共组件                     |
| 目录 L0/L1 摘要                  | content tab                                                    | 组件不建目录概念                           |
| 字符数 / 更新时间 footer         | `slots.footer`（新增）                                         | 见 5.8                                     |
| 权限标记 / 管理入口              | `slots.titleExtra` / `slots.actions`                           | 已够                                       |
| 关闭按钮 / 版本历史              | `slots.actions`                                                | 已够                                       |
| 空文件 vs 空白内容               | 组件判定 + 两条 labels                                         | 见 5.9                                     |
| 加载失败 / 不支持 / 确认态       | `slots.loading` / `error` / `downloadFallback` / `confirm`     | 分支把三种都指向同一个 fallback，见 5.10   |
| 内置文案（加载 / 兜底）          | `locale` 透传 + `slots` 接管                                   | 不改底座，见 5.3                           |
| 编辑（后端 `isEdit` + 50k 上限） | `editing`，编辑区不用底座                                      | 上限归宿主                                 |
| 任意 ReactNode 走同一布局        | 自定义 adapter + `SKIP_ASSESS`（`CompatibleFilePreview`）      | 见 5.17                                    |

## 五、遗漏与需要决策的点

### 5.1 面包屑：对齐火山版，逐段可点（已定，按火山版实现）

**结论：对齐火山版，不做取舍。** 回调签名 `openParent(uri)` 本身够用——它已经带一个 URI 入参，问题只是「注释和实现都把它当成了『上一级』」。

**火山版事实**（`UriBreadcrumb.tsx:124-200`）：面包屑把路径拆成 N 段，**每一段都是独立 `<button>`**，点击时调 `onOpenParent(pathAt(index))`：

```ts
const pathAt = (index: number) =>
  `viking://${segments.slice(0, index + 1).join('/')}`;
```

即把**被点那一段的完整祖先 URI** 通过入参传出去。宿主侧 `handleOpenMarkdownVikingUri(uri) → navigateToNode(getContextNodeId(uri))`，按 URI 定位节点。

**要做的**：组件内部按 `source.path` 逐段拆分、每段渲染按钮、点击传该段前缀 URI，宿主一个 `openParent` 就能处理任意层级，不需要第二个回调。需要改的只是 `FilePreviewOperations.openParent` 和 `onOpenParent` 的**注释**——从「打开当前文件的上级目录」改成「打开给定的祖先 URI」。

**一并要对齐的火山版行为**：

| 行为       | 火山版做法                                               | 是否进公共组件 |
| ---------- | -------------------------------------------------------- | -------------- |
| 逐段可点   | 每段 `<button>`，传 `pathAt(index)`                      | 是             |
| 溢出省略   | 测量后把放不下的段收进「…」下拉，每项同样传祖先 URI      | 是             |
| 截断 hover | `scrollWidth > clientWidth` 时用 Popover 显示全名        | 是             |
| 切换动画   | 160ms `translateX` 位移，`prefers-reduced-motion` 时跳过 | 是             |
| 根前缀展示 | 首段显示 `viking://` 而不是裸段名                        | 是             |
| 当前段     | `aria-current="page"`，不可点                            | 是             |

**全部对齐，动画也做。** 这套组件最终服务 `studio`，不是通用基础库，凡火山版有且用户能感知的交互都应一致。动画实现要点：

- 只在 `segments + title` 的签名不变、仅 `left` 变化时触发（火山版用 `previousPosition` ref 存上一次的签名和左偏移），签名变化说明是切了节点，不做位移
- `window.matchMedia('(prefers-reduced-motion: reduce)').matches` 为真时跳过
- 用 `Element.animate()` 返回的 `Animation`，effect 清理函数里 `animation.cancel()`，避免快速切换时动画叠加
- 位移量是 `previous.left - left`，从旧位置滑到新位置，配合 `opacity: 0.65 → 1`

溢出测量用隐藏的 `measure` 行拿自然宽度，再按可用宽度从右往左丢段，与火山版同一套算法（`UriBreadcrumb.tsx:61-99`）。

### 5.2 缺受控 `activeKey`（建议补）

file-browser 的当前资源由 URL / 宿主 state 决定，只有 `ref.setActiveTab` 是命令式的，宿主无法声明式驱动。

建议增加：

```ts
activeKey?: string;
defaultActiveKey?: string;
onActiveKeyChange?: (key: string) => void;
```

### 5.3 底座内置文案：不改底座，用 `locale` + `slots` 覆盖（已定）

**结论：不给 `FileViewer` 加 `messages`，照火山版分支的做法。**

对照 `codex/feat/team-compile-frontend-20261024` 的 `ContextFileViewerPreview.tsx`：它**没有**改底座文案，而是两件事：

```tsx
<FileViewerCore
  locale={I18n.language.startsWith('en') ? 'en-US' : 'zh-CN'}
  slots={slots}
  toolbar={false}
  ...
/>
```

1. **`locale` 透传**：宿主把当前语言给底座，底座内置的 en-US / zh-CN 两份表就够用。火山版控制台只有中英两种语言，这已经覆盖。
2. **`slots` 接管兜底态**：`confirm` / `downloadFallback` / `error` 三个都指向同一个 `fallback` 节点，文案来自宿主自己的 `I18n.t`，彻底绕开底座文案。

**这套做法的适用边界**：

| 状态                         | 分支怎么处理                             | 我们的组件                                                                |
| ---------------------------- | ---------------------------------------- | ------------------------------------------------------------------------- |
| `loading`                    | 不接管，吃底座 `loading.*` 文案          | 同：透传 `locale`                                                         |
| `confirm`（大文件）          | `slots.confirm` → 宿主 fallback          | 组件暴露 `slots.confirmFallback`，宿主可覆盖；不传则吃底座文案 + `locale` |
| `downloadFallback`（不支持） | `slots.downloadFallback` → 宿主 fallback | 同上                                                                      |
| `error`                      | `slots.error` → 宿主 fallback            | 同上                                                                      |
| 工具栏 label（`toolbar.*`）  | 无关，`toolbar={false}`                  | 无关                                                                      |

**所以剩下的「不可控」只有 `loading.*` 和 `text.partial`**，而这两个：

- 只在加载瞬间和部分预览时出现
- 有 `locale` 兜底，中英都对
- 宿主真想改，`slots.loading` 可以整体接管

**组件的接口决定**：

- 可选 `locale: 'zh-CN' | 'en-US'`（继承上层，最终默认 `en-US`） 选择组件内置中英文文案，直接透传给 `FileViewerCore`
- `labels` 按项覆盖组件外壳文案（页签、标题行、按钮、空态），未覆盖项跟随 `locale`
- 兜底态通过 `slots.loadingFallback` / `errorFallback` / `unsupportedFallback` 暴露；`confirm` 和 `downloadFallback` 因为带回调参数，用 `ComponentType<...>` 形态暴露，与底座对齐

**代价**：底座文案不可逐条定制，只能整体跟随 `locale`。火山版接受了这个限制，我们照做。

**如果将来确实需要**：再给底座加 `messages`（方案见下），但当前不做。

#### 备选方案（暂不采用）：给底座加 `messages`

`shared/i18n.ts` 的 `MESSAGES` 是两份硬编码 `Record<MessageKey, string>`，`createT(locale)` 只按 locale 查表，`createT` 有 12 处调用点（main 实测）：`FileViewerCore`、`FileViewerRenderer`，以及 `adapters/{code,markdown,html,text,json,word}` 的工厂与 Viewer、`MediaViewer`。

改法是加 `messages?: Partial<Record<MessageKey, string>>` 并在 `createT` 里优先取 override，难点在透传（Core → Renderer、Core → `AdapterSetupCtx`）。另外 `adapters/cad/messages.ts` 是独立文案表，不走 `createT`。

留作将来需要逐条定制时的方案。

### 5.4 预览/源码切换：底座受控 `viewMode` 已可用（已解决）

**要解决的问题**：组件的「预览/源码」按钮在标题行，而真正决定视图的是 `FileViewerCore`。两者必须同步。

**已解决**：`@ark-design/ui` main 的 `13c3ba9`（MR !314，`codex/file-viewer-controlled-view-mode`）已合入：

```ts
// core/types.ts — FileViewerCoreProps
/** 受控视图模式；传入后由调用方通过 onViewModeChange 更新。
 *  取值需在当前 Adapter 声明的 supportedViewModes 内，否则回退到可用模式中的默认项。 */
viewMode?: ViewerViewMode;
/** 工具栏切换或 Adapter 主动降级请求改变视图模式时调用。 */
onViewModeChange?: (viewMode: ViewerViewMode) => void;
```

实现是 `core/shell/useViewMode.ts`，比我们原先设想的更完整，多了两条：

1. **Adapter 主动降级也会通知**：Word 版式渲染失败退回阅读视图时，会通过 `onViewerStateChange` 上报，受控模式下 Core 把它当作一次切换请求交给调用方决定是否采纳。用 `previousReportedRef` 只响应渲染期的新上报，避免切换 source 时的旧值误报。
2. **`changeViewMode` 会校验 `availableViewModes`**，不在可用列表里的值直接丢弃，不只是回退。

`FileViewerCore.tsx:80-89` 接线，`FileViewerToolbar.tsx:268` 的 `Radio.Group` 改为调 `changeViewMode`。

**组件这边的用法**（拿到新版后）：

```tsx
<FileViewerCore
  viewMode={viewMode}
  onViewModeChange={setViewMode}
  toolbar={false}
  ...
/>
```

`adapterWithViewMode` 整套包装、`supportedViewModes` 过滤、每次切换重建 adapters 全部不需要。

**已就位**：`0.0.0-beta.6` 已发到 registry（`beta` dist-tag），本仓库依赖已升级到 `0.0.0-beta.6`，`core/types.d.ts:725-733` 确认含这两个 prop，`FileViewerCore.js` 与 `FileViewerToolbar.js` 的接线也已编译进产物。

**为什么要等底座而不是在组件层绕过**：绕过方案（包 adapter 的 `getDefaultViewMode`）每次切换都会重跑整条流水线——`resolveSource()` 新建实例 → `adapter.load()` 重新 import → `prepare()` 重新解析 → `workState` 从 `ready` 退回 `loading` → `FileViewerRenderer` 卸载重建。滚动位置丢失（已确认可接受），但闪一下和延迟可感知，且组件层要背一整套 workaround。

### 5.5 `source` 缺截断标记（建议补）

宿主按 limit 读取时（火山版默认 10000 行、只读 10000 字符）内容是被截断的，组件无从得知，会把「读了一半」当成完整文件渲染。

建议 `source.truncated?: boolean`，为真时正文底部显示 `labels.truncated`（默认 "Content truncated"）。同时 `labels.truncated` 需要新增。

### 5.6 会话 JSONL 必须走 content tab（需文档说明）

底座的 json adapter 同时吃 `.jsonl`，会把 `messages.jsonl` 渲染成 JSON 树。火山版把它渲染成对话列表。

组件不做业务判断，因此文档必须写清：`messages.jsonl` 这类结构化日志要用 `kind: 'content'` 自行渲染，用 `kind: 'file'` 得到的是树形 JSON 视图。

### 5.7 图片与音视频：渲染进组件，增强能力不进（已定）

**渲染已接入组件。** `image()` 与 `media()` 都在 adapters 列表里：

- 图片是**内容式** Adapter（`mode: 'content-based'`），支持 png / apng / jpg / jpeg / webp / gif / avif / svg，另有 `sniffBitmap` 兜底识别。
- 音视频是**引用式** Adapter（`mode: 'reference-based'`），支持 mp4 / mp3 / wav。
- 底座在 `resolveForAdapter` 里按 Adapter 的 `mode` 自动把 `source` 在 `ResolvedContent` 与 `ResolvedReference` 之间转换，宿主只给 `content` / `file` / `url`，不用关心读取方式。

**增强能力仍不进组件**：

| 能力              | 归属         | 理由                                      |
| ----------------- | ------------ | ----------------------------------------- |
| 上一张/下一张图片 | file-browser | 依赖「同目录兄弟文件列表」，是模块数据    |
| 相邻图片预加载    | file-browser | 同上                                      |
| 视频抽帧列表      | 业务扩展     | 依赖后端的 `segment_info.json` 与抽帧产物 |
| 视频转录同步      | 业务扩展     | 业务数据与交互，通用预览不认识            |

宿主需要这些能力时，在预览区外组合或通过 `slots.actions` / `slots.footer` 注入，组件不感知兄弟文件与业务格式。

### 5.8 缺 footer 插槽（建议补）

火山版 footer 有字符数、更新时间、保留策略控件。当前 `slots` 只有 `actions` 和 `titleExtra`，都在标题行。

建议 `slots.footer?: ReactNode`，渲染在正文下方、与正文同宽。

### 5.9 空态文案缺失（建议补）

当前 `labels` 没有「文件为空」和「暂无内容」两条，而这正是火山版专门修过的区分。需要新增：

```ts
fileEmpty?: string;   // 严格空串
noContent?: string;   // 空白内容
```

### 5.10 `slots` 缺 confirm / downloadFallback（建议补）

底座有四种非正常态：`loading` / `error` / `confirm`（大文件确认）/ `downloadFallback`（无法内联预览）。当前 API 只暴露三种，缺后两种。宿主无法定制「文件太大，是否继续」和「此格式请下载」。

建议补 `slots.confirmFallback` 与 `slots.downloadFallback`，形态用 `ComponentType<...>` 与底座对齐，而不是 `ReactNode`（需要回调参数）。

### 5.11 ref 句柄偏窄（建议补）

底座 `ViewerHandle` 有 `page` / `search` / `zoom` / `selectionTool`。PDF 场景宿主常需要「跳到第 3 页」。当前句柄只有 `setActiveTab` / `getActiveTab`。

建议增加 `getViewerHandle(): ViewerHandle | null`，透传底座句柄，不做二次封装。

### 5.12 图标集（另一个分支在做，与本次并行）

自动按格式选图标是 API 承诺的行为，但本仓库只有 5 个文件类型图标（audio/pdf/presentation/video/word），md / json / code / image / 目录 / table / cad / 3d 都没有。

已在另一个分支提交图标改动（含 `folder-open.svg` 的 clipPath 修正、`?inline` + CSS mask 方案），**不在本次范围内，但它是开工前置**：组件的「按格式自动选图标」依赖图标集就位。

两边的衔接点：

- 页签图标由宿主通过 `icon` 提供，组件不按文件名推断类型，也不做兜底
- 省略 `icon` 时页签只显示标题，不占图标位
- 需要按格式取图标的宿主从 `@openviking/react/icons` 引入 `getFileTypeIcon`，把结果传给 `icon`；这样只装文件预览的消费者不会被拖进整包图标（约 74 KB）

### 5.13 样式与 peer 的消费成本（已定：接受）

- 底座样式必须由消费者引入，分支用的是 `@ark-design/ui/file-viewer/style/css`（已确认可解析）。组件 JS 不隐式加载 CSS，与仓库约定一致，但文档必须写，否则预览区无样式。
- `@ark-design/ui` 的 peer 里 `three`、`@arco-design/web-react` **不是 optional**。决定：接受，不做按需 peer 改造。

**这两个依赖的真实成本**（实测 main 与已装版本）：

| 依赖                     | 谁用                                                                                                           | 是否按需加载              | 我们的场景                                                                                                             |
| ------------------------ | -------------------------------------------------------------------------------------------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `@arco-design/web-react` | `FileViewerStateContent` 静态 `import { Button }`，`FileViewerToolbar` / `ZoomControls` / `Loading` 也静态引用 | **否，Core 一挂载就加载** | 必需。即使 `toolbar={false}` 也躲不掉，因为 `StateContent` 是 loading / confirm / error / unsupported 四种状态的渲染者 |
| `three`                  | 只在 `adapters/cad/**` 静态 import；`model-3d` 走 `await import()`                                             | **是，按需**              | 不接 CAD / 3D 就永远不会加载，但 peer 声明仍会要求安装                                                                 |

所以：

- **arco 是硬依赖**，装 file-preview 就一定需要。
- **three 是「声明必需、运行按需」**。装包时 npm 会提示需要，但不接 CAD/3D 时运行时不会加载。

要在 README 里如实写这两条，避免消费者困惑。

### 5.14 色板：组件自带变量，不依赖三方 UI 库（已定）

组件库不绑定第三方 UI 库和它的样式，外壳与正文各自独立配色。

**外壳**用自己的 `--ov-ui-file-preview-*` 变量，在根节点集中声明，每个都带兜底值，宿主不提供也能正常显示。叠加色取中灰加透明度（明暗主题通吃），次要文字从继承的文字色派生：

| 变量                              | 兜底值                                     |
| --------------------------------- | ------------------------------------------ |
| `--ov-ui-file-preview-accent`     | `#2563c5`                                  |
| `--ov-ui-file-preview-border`     | `rgb(128 128 128 / 20%)`                   |
| `--ov-ui-file-preview-row-hover`  | `rgb(128 128 128 / 12%)`                   |
| `--ov-ui-file-preview-surface`    | `canvas`                                   |
| `--ov-ui-file-preview-text-muted` | `color-mix(currentColor 60%, transparent)` |
| `--ov-ui-file-preview-focus-ring` | `#2563c5`                                  |
| `--ov-ui-radius`                  | `6px`                                      |
| `--ov-ui-font-mono`               | `ui-monospace, monospace`                  |

**正文**由底座渲染，它的 `--agentic-file-viewer-*` 指向 Arco 的 `--color-*` / `--primary-*`。这些变量定义在 `.ark-agentic-file-viewer`（本组件的子级）上，宿主需要在应用入口提供 Arco 主题层，否则正文会退化成浏览器默认色。

**结论**：外壳完全自主，不依赖 Arco；正文的配色取决于宿主是否提供主题层，这是底座的既有约束，组件层不做兜底。

### 5.15 `capabilities` 基本失效（已随工具栏关闭）

关掉工具栏后 `fullscreen` / `page` / `search` / `zoom` 都没有 UI 入口。`download` 仍会影响 error 态里的下载按钮（`FileViewerStateContent` 的 `error` 分支），分支里给 `capabilities={{ download: file !== undefined && kind !== 'markdown' }}` 就是这个用途。

建议：组件不暴露完整的 `capabilities`，只在内部按 `operations.download` 是否存在来决定 `download` 开关。

### 5.16 Markdown 图片的正确注入方式（分支已验证）

分支没有用 `processSrcBeforeLoad`，而是走 `resources` + `getObjectURL`（`contextMarkdownAdapter.tsx:167-215`）。原生 `MarkdownViewer` 的取图顺序是：

```js
const resource = resources.get(src);
if (resource) return resource.getObjectURL(); // ← 优先走 resources
return (await imageOptions?.processSrcBeforeLoad?.(next)) || next;
```

分支的做法是：在 `prepare` 阶段把 markdown 解析出的图片目标收集成 `destinations`，然后在渲染组件里把它们塞进 `resources` Map，每个 key 对应的值是一个 `Proxy`，只覆盖 `getObjectURL` 指向鉴权 blob loader，其余方法绑定到原 `content`。

比 `processSrcBeforeLoad` 好在：

- 只在预览视图生效，源码视图不加载任何图片
- 有 epoch + `AbortSignal` 生命周期，切文件时 `revokeObjectURL`
- 同名图片不会跨文件串味

`model3d` 的伴随资源同理：分支用 `modelAdapterWithResourcePaths` 把 `File[]` 挂到 `prepared.resourceFiles`，注释说明 beta.0 会丢目录，所以保留相对名避免同名冲突。

### 5.17 content tab 是否套 `CompatibleFilePreview`（已定：不套）

分支里的 `CompatibleFilePreview` 用一个假 adapter 把任意 ReactNode 塞进 Core：

```ts
const adapter: Adapter = () => ({
  name: FILE_TYPE,
  match: { fileTypes: [FILE_TYPE] },
  mode: 'reference-based',
  assess: SKIP_ASSESS, // 跳过内容准入，不读文件
  load: async () => ({ Component: PreviewContent }),
});
```

**不套的实际后果**（这是需要判断的点）：

Core 的滚动与布局并不在 `__body` 上。实测 `style/file-viewer-core.less:905-1005`：

```less
.file-viewer__body {
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
} // 无 overflow
.file-viewer__render-stage,
.file-viewer__render-content {
  display: flex;
  flex: 1;
  position: relative;
  min-height: 0;
  min-width: 0;
} // 无 overflow
```

真正的滚动容器在**各 adapter 渲染器自己**（markdown 的 `MDRenderer`、code 的 `CodeViewer`）。所以「套上 Core 就能获得统一滚动」这个前提本身不成立——套了也只有一层 flex 包裹，宿主节点照样要自己处理滚动。

**Core 真正提供的**只有：`__body` 的 flex 布局约束（`flex:1` + `min-height:0`，保证子节点不撑破容器）、以及四种状态（loading / confirm / error / unsupported）的渲染位置。

**结论：不套。** 理由：

1. 滚动一致性拿不到（上面已证明）
2. content tab 的加载 / 错误态本来由宿主提供（`content` 是完整节点，含它自己的骨架和错误提示），套上后反而有两套状态机
3. 每套一层 Core 就多一个 `section` + 若干 div，还有 `identity` 变化重置状态的风险

**唯一要补的**：组件给 content tab 的容器加与 file tab 一致的 `flex:1; min-height:0; min-width:0; overflow:auto`，让宿主节点的布局环境对齐。这是纯 CSS，不需要引入假 adapter。

## 六、落地顺序与外部依赖

### 外部依赖（不解决就不能接 `FileViewerCore`）

| 依赖                                | 状态                   | 阻塞范围                                 |
| ----------------------------------- | ---------------------- | ---------------------------------------- |
| `@ark-design/ui` 受控 `viewMode`    | **已就位**（`beta.6`） | 无                                       |
| 图标集（`@openviking/react/icons`） | 另一分支在做           | 「按格式自动选图标」；可先回退到通用图标 |

图标不阻塞外壳、类型、空态、编辑态——这些不依赖图标。接 `FileViewerCore` 也不需要图标。

### 顺序

1. **写 `types.ts`**：补齐 5.2（受控 `activeKey`）/ 5.5（`truncated`）/ 5.8（`footer`）/ 5.9（空态文案）/ 5.10（confirm / downloadFallback 插槽）/ 5.11（`getViewerHandle`）/ `locale`。
2. **写外壳**：tab row + header（面包屑逐段可点 + 溢出下拉 + 切换动画）+ 空态 + 编辑态，正文先接纯文本。这一步不依赖底座新版本。
3. **接 `FileViewerCore`**：按分支的 12 种 kind 建 adapters 表，markdown 走 `resources` 注入鉴权图片，`toolbar={false}`，`locale` 透传，`slots` 接管兜底态。
4. **接视图切换**：直接用受控 `viewMode`（`beta.6` 已支持）。
5. **图标由宿主提供**：`icon` 省略时不显示，组件不内置映射表。
6. **补文档**：5.6 的 content tab 约定、5.13 的样式与 peer、5.14 的 `arco-theme` 要求。
7. **文档站示例**：空文件 / 空白内容 / 不支持格式 / 大文件 / 编辑失败 / 暗色。
