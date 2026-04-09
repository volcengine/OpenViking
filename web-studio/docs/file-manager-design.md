# Viking File Manager 设计文档

日期：2026-04-08

## 概述

在 web-studio 中实现一个三栏布局的文件管理器，参考 macOS Finder 的交互模式，方便用户快捷浏览 viking:// 虚拟文件系统。

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Viking File Manager                             │
├──────────┬────────────────────┬────────────────────────────────────────┤
│ 左侧树    │    中间列表         │          右侧预览                       │
│          │                    │                                        │
│ ▼ viking │  📁 docs/   📁 src │  ┌─────────────────────────────────┐   │
│   ▼ docs │  📄 README.md      │  │ filename: README.md             │   │
│     📁 p │  📄 config.json    │  │ size: 1.2KB  |  type: Markdown  │   │
│     📁 t │  📄 package.json   │  ├─────────────────────────────────┤   │
│   📁 src │                    │  │ tags: [project, docs]           │   │
│          │                    │  ├─────────────────────────────────┤   │
│          │                    │  │ .abstract: 项目说明文档...       │   │
│          │                    │  ├─────────────────────────────────┤   │
│          │                    │  │ ## 内容预览                     │   │
│          │                    │  │ # README                        │   │
│          │                    │  │ ...                             │   │
│          │                    │  └─────────────────────────────────┘   │
└──────────┴────────────────────┴────────────────────────────────────────┘
```

## 功能需求

### 1. 左侧目录树 (FileTree)

- 展示 viking:// 虚拟文件系统的目录结构
- 支持懒加载：展开节点时动态请求子目录
- 自动展开：根据 currentUri 自动展开到对应路径
- 选中高亮：当前路径在树中高亮显示
- 使用 shadcn/ui Collapsible 实现展开收起

### 2. 中间文件列表 (FileList)

- 展示当前目录的文件和子目录
- 使用现有 TanStack Table 组件
- 列：文件名、size、modTime、tags
- 点击目录：跳转到该目录（更新 currentUri）
- 点击文件：触发右侧预览

### 3. 右侧预览面板 (FilePreview)

- 点击文件时显示在右侧面板
- 根据文件类型选择渲染方式：
  - **图片** (jpg/png/gif/webp/svg) → `<img />` 原生展示
  - **Markdown** (md/markdown) → ReactMarkdown 渲染
  - **代码** (js/ts/py/go/rs/json/yaml 等) → 语法高亮
  - **其他** → 原始文本展示
- 显示 stat 信息：文件名、大小、修改时间
- 显示 tags（如果存在）
- 显示 .abstract 摘要内容
- 显示文件内容（调用 read 接口）
- 支持关闭按钮

## 数据结构

### FsEntry 类型

```typescript
// web-studio/src/lib/legacy/data-utils.ts

interface FsEntry {
  uri: string        // viking://resources/xxx/
  size: string       // 文件大小
  isDir: boolean     // 是否目录
  modTime: string    // 修改时间 "2026-02-11 16:52:16"
  abstract?: string  // L0 摘要
  tags?: string      // 资源标签 (仅目录且是 resource root 有)
}
```

### API 调用

```typescript
// 获取目录树（用于左侧树）
getFsTree({
  query: {
    uri: 'viking://',
    output: 'agent',
    level_limit: 3,  // 预加载深度
  },
})

// 获取目录内容（用于中间列表）
getFsLs({
  query: {
    uri: currentUri,
    output: 'agent',
    show_all_hidden: true,
  },
})

// 获取文件内容（用于右侧预览）
getContentRead({
  query: {
    uri: fileUri,
    offset: 0,
    limit: -1,
  },
})
```

## 组件设计

### 文件结构

```
web-studio/src/
├── components/viking-fm/
│   ├── VikingFileManager.tsx   # 父组件，三栏布局 + 状态管理
│   ├── FileTree.tsx            # 左侧目录树
│   ├── FileTreeNode.tsx        # 树节点组件
│   ├── FileList.tsx            # 中间文件列表
│   ├── FilePreview.tsx         # 右侧预览面板
│   ├── PreviewImage.tsx        # 图片预览组件
│   ├── PreviewMarkdown.tsx     # Markdown 预览组件
│   ├── PreviewCode.tsx         # 代码预览组件
│   ├── PreviewText.tsx         # 文本预览组件
│   └── index.ts                # 统一导出
```

### 组件接口

#### VikingFileManager

```typescript
interface Props {
  // 可选：初始路径，默认 viking://
  initialUri?: string
}

// 管理状态：
// - currentUri: 当前目录路径
// - previewFile: 当前预览的文件
// - expandedKeys: 树展开的节点集合
```

#### FileTree

```typescript
interface FileTreeProps {
  currentUri: string           // 当前路径，用于高亮
  onSelect: (uri: string) => void  // 点击目录跳转
}
```

#### FileList

```typescript
interface FileListProps {
  uri: string                   // 当前目录路径
  onFileClick: (file: FsEntry) => void  // 点击文件
  onDirectoryClick: (uri: string) => void  // 点击目录
}
```

#### FilePreview

```typescript
interface FilePreviewProps {
  file: FsEntry | null         // 要预览的文件
  onClose: () => void          // 关闭预览
}
```

## 组件间通信

```typescript
// VikingFileManager.tsx
function VikingFileManager() {
  const [currentUri, setCurrentUri] = useState('viking://')
  const [previewFile, setPreviewFile] = useState<FsEntry | null>(null)
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set())

  // 左侧树选中目录 → 更新当前路径
  const handleTreeSelect = (uri: string) => {
    setCurrentUri(uri)
    setPreviewFile(null)
  }

  // 中间列表点击文件 → 触发预览
  const handleFileClick = (file: FsEntry) => {
    setPreviewFile(file)
  }

  // 中间列表点击目录 → 跳转
  const handleDirectoryClick = (uri: string) => {
    setCurrentUri(uri)
    setPreviewFile(null)
  }

  return (
    <div className="flex h-full">
      <FileTree
        currentUri={currentUri}
        expandedKeys={expandedKeys}
        onExpand={(uri) => setExpandedKeys(prev => new Set([...prev, uri]))}
        onSelect={handleTreeSelect}
      />
      <FileList
        uri={currentUri}
        onFileClick={handleFileClick}
        onDirectoryClick={handleDirectoryClick}
      />
      <FilePreview
        file={previewFile}
        onClose={() => setPreviewFile(null)}
      />
    </div>
  )
}
```

## 文件类型判断

```typescript
type FileType = 'image' | 'markdown' | 'code' | 'text'

function getFileType(filename: string): FileType {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  
  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'ico']
  const markdownExts = ['md', 'markdown']
  const codeExts = [
    'js', 'ts', 'jsx', 'tsx', 'mjs', 'cjs',
    'py', 'go', 'rs', 'java', 'cpp', 'c', 'h', 'hpp',
    'json', 'yaml', 'yml', 'toml', 'xml',
    'html', 'css', 'scss', 'less',
    'sh', 'bash', 'zsh', 'sql', 'graphql'
  ]

  if (imageExts.includes(ext)) return 'image'
  if (markdownExts.includes(ext)) return 'markdown'
  if (codeExts.includes(ext)) return 'code'
  return 'text'
}
```

## 依赖安装

```bash
# 需要用到的现有依赖（已安装）
# - @tanstack/react-query (TanStack Query)
# - @tanstack/react-table (TanStack Table)
# - shadcn/ui 组件 (Collapsible, Table, Card, Dialog 等)
# - lucide-react (图标)

# 可能需要新增的依赖
npm install react-markdown        # Markdown 渲染
npm install react-syntax-highlighter  # 代码高亮
# 或
npm install @uiw/react-md-editor   # Markdown 编辑器（带预览）
npm install prismjs               # 代码高亮
```

## 实现顺序

1. **VikingFileManager** - 搭建三栏布局框架
2. **FileList** - 实现中间列表（最简单，可复用现有逻辑）
3. **FileTree** - 实现左侧目录树（核心难点：懒加载）
4. **FilePreview** - 实现右侧预览（根据类型渲染）
5. **集成与优化** - 状态同步、URL 同步、动画

## 预估工作量

| 组件 | 工作内容 | 预估 |
|------|----------|------|
| VikingFileManager | 三栏布局、状态管理、URL 同步 | 0.5 天 |
| FileList | 列表展示、点击事件 | 0.5 天 |
| FileTree | 树结构、懒加载、自动展开、选中高亮 | 1 天 |
| FilePreview | 类型判断、多格式渲染、meta 展示 | 1 天 |
| **总计** | | **~3 天** |

## 路由集成

```typescript
// web-studio/src/routes/files.tsx
import { FileSystemPage } from '#/components/viking-fm'

export default function FilesPage() {
  return <VikingFileManager />
}
```

在 router 中添加路由：
- `/files` - 三栏文件管理器
- 或复用现有 `/data/filesystem` 路由

## 边缘场景与容错策略

> 本节按 **Required（本期必做）** 与 **Optional（可选）** 划分。Optional 为增强项，不阻塞首版上线。

### Required（本期必做）

1. **URI 规范化与一致性**
   - 所有目录 URI 统一走 `normalizeDirUri`（含尾 `/` 处理）。
   - 对 URL query 中的 URI 做 encode/decode 一致化，避免中文、空格、`#`、`?` 导致树高亮/展开错位。
   - 比较路径时一律比较 normalized URI。

2. **超大目录性能保护**
   - `getFsLs` 增加 `node_limit`（或 `limit`）默认上限，避免一次拉全量。
   - 中间列表使用分页或虚拟滚动（至少二选一）。
   - 当目录项超过阈值时提示“已展示前 N 项，可继续加载/翻页”。

3. **大文件与二进制预览保护**
   - 预览前先依据 `stat.size` 判断，超过阈值（如 2MB）不自动全量 `read`。
   - 大文件默认只读前 N 行（如 500 行）并提供“继续加载”。
   - 二进制/不可读文本显示“该文件类型不支持文本预览”，提供下载或外部打开入口。

4. **排序正确性**
   - 列表展示可保留字符串，但排序必须基于原始值：
     - `sizeBytes: number`
     - `modTimestamp: number`
   - 避免字符串字典序导致 `10KB < 2KB` 等错误排序。

5. **空状态与特殊文件名**
   - 空目录：显示明确空态与下一步操作（刷新/返回上级）。
   - 空文件：预览区显示“空文件（0 bytes）”。
   - 无扩展名文件（README、LICENSE、Dockerfile）：按文本预览处理。

6. **Markdown 安全策略**
   - 默认不渲染危险 HTML；如需支持 HTML，必须加 sanitize 白名单。
   - 外链默认 `rel="noopener noreferrer"`，防止潜在安全风险。

7. **树结构异常保护**
   - Tree 展开维护 `visited` 集合，防止异常循环结构导致重复递归。
   - 设置最大展开深度（例如 20 层）作为兜底保护。

8. **缓存与失效策略**
   - 目录查询按 URI 缓存（React Query key: `['fs-ls', uri]`）。
   - 失效条件：手动刷新、URI 变化、TTL 到期。
   - 文件预览缓存按 `uri + modTime` 区分，文件变更后自动失效。

9. **移动端状态保持**
   - 单栏模式切换 Tree/List/Preview 时，`currentUri` 与 `previewFile` 不丢失。
   - 返回列表后保持滚动位置（至少保持当前目录与选中项）。

### Optional（可选增强）

1. **请求竞态的细粒度防护**
   - 列表可依赖 `useQuery` + queryKey；预览建议使用 `enabled query` 或“仅接受最后一次点击结果”策略。

2. **文件已移动/删除即时提示**
   - 点击后 `read/stat` 失败时给出专门文案：“文件可能已移动或删除，请刷新目录”。

3. **401/403 鉴权细分展示**
   - 现阶段先统一错误提示；若后端明确资源级权限模型，再细分“未登录 / 无权限 / 资源不可见”三类态。

## 注意事项

1. **虚拟文件系统延迟** - AGFS 聚合了多种后端（S3、KV 等），可能有延迟，需要骨架屏
2. **URL 同步** - 将 currentUri 同步到 URL query params，支持刷新保持、分享链接
3. **响应式** - 移动端考虑改为单栏 + 底部标签切换