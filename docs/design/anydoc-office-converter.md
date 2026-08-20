# anydoc Office 转换层设计

**范围**: 用 [firecrawl/anydoc](https://github.com/firecrawl/anydoc) 替换 OpenViking 内置 Office/EPUB 的「格式 → Markdown」转换层，并按现有媒体链路补齐嵌入图片落盘与 Markdown 引用。
**状态**: 已实现。
**日期**: 2026-08-12

---

## 1. 背景与问题

历史 Office/EPUB 解析多为 markitdown 风格的自研转换：每类格式各自依赖专用 Python 库，再交给 `MarkdownParser` 做结构切分与落盘。当前实现已经删除这些旧 parser 和生产依赖，Office/EPUB 唯一路径为 `AnyDocParser`。

痛点：

1. 转换质量不稳定（表格、标题层级、列表、脚注等）。
2. 格式覆盖偏窄（例如老 `.ppt`、ODT/RTF、部分 OOXML 变体）。
3. 各 Parser 各自实现 `_convert_to_markdown`，行为不一致。

anydoc 是纯 Rust 的 Office→GFM 转换库，提供 Python 绑定 `firecrawl-anydoc`，质量与速度基准较好，且输出统一。但其默认 `to_markdown` **不会**把嵌入图片写成可用的本地路径引用：图片字节在 `document.assets`，Markdown 里通常只剩 alt 文本。这与 OV 现有「抽图 → `storage.save_image` → `![name](rel_path)` → `MarkdownParser` 摄入」不一致，需要桥接层补齐。

PDF 方面：anydoc 的 PDF 走 `pdf-inspector`，且 **不支持** `to_document`（无 assets 模型）。本方案明确 **不替换** `PDFParser`（继续 pdfplumber / MinerU / Understanding）。

---

## 2. 目标与非目标

### 目标

1. Office/EPUB 的「格式 → Markdown」统一走 anydoc。
2. 嵌入图片按 OV 现有设计落盘，并在 Markdown 原位置保留 `![alt](rel_path)`。
3. 下游仍使用现有 `MarkdownParser` → `TreeBuilder` → 语义队列，不改解析哲学。
4. 扩展常用 Office 变体扩展名，并更新中英文资源文档。
5. 以可合并 PR 的标准交付：聚焦改动、测试覆盖、文档同步。

### 非目标

1. 不替换 `PDFParser`、MinerU、Understanding、HTML/网页、飞书、代码仓、音视频解析。
2. 不重写 `MarkdownParser` 切分/落盘逻辑。
3. 不接入 Firecrawl 托管 OCR / Parse API。
4. 不在 AnyDoc 失败时回退旧 Office/EPUB parser；旧 parser 已删除。

---

## 3. 总体架构

```text
Accessor → LocalResource
              │
              ▼
        ParserRouter
              │
     ┌────────┴────────┐
     │                 │
 AnyDocParser      PDFParser
 Office/EPUB       (不变)
     │
     ▼
 AnyDocConverter.convert(path, resource_name, storage)
   ├─ read bytes
   ├─ anydoc.to_document(bytes[, format])
   ├─ AnyDocMarkdownRenderer: serialize Document → GFM
   │    └─ Inline.Image(asset) → storage.save_image
   │         → ![alt](rel_to_media_dir)
   └─ return markdown
     │
     ▼
 MarkdownParser.parse_content(
   markdown,
   allowed_media_dirs=[storage.media_dir],
   ...
 )
     │
     ▼
 TreeBuilder → SemanticQueue
```

核心模块：

- `openviking/parse/parsers/anydoc.py`：统一 Office/EPUB parser 主入口。
- `openviking/parse/parsers/anydoc_converter.py`：同步执行 anydoc 转换、调用 renderer、组装转换结果。
- `openviking/parse/parsers/anydoc_renderer.py`：把 AnyDoc document model 序列化为 Markdown，并处理图片引用改写。

职责边界：

| 组件 | 负责 | 不负责 |
|------|------|--------|
| `AnyDocParser` | Office/EPUB 扩展名注册、`parse()` 编排、委托 `AnyDocConverter` 和 `MarkdownParser` | 具体 Office 字节解析 |
| `AnyDocConverter` | anydoc 调用、错误映射、调用 renderer、组装 `AnydocConversionResult` | Markdown 细节渲染、切分、VikingFS、向量化 |
| `AnyDocMarkdownRenderer` | document→GFM、图片落盘与引用改写、表格/列表/脚注/锚点输出 | 文件格式识别、调用 anydoc、调用 `MarkdownParser` |
| `MarkdownParser` | 结构切分、本地图片摄入、temp 树 | Office 格式理解 |
| `PDFParser` | 本地/远程 PDF（含抽图） | — |

---

## 4. AnyDocConverter 设计

### 4.1 公共 API

```python
@dataclass(frozen=True)
class AnydocConversionResult:
    markdown: str
    images_saved: int
    source_format: str  # anydoc 检测到的格式名，如 "docx"


class AnyDocConverter:
    def convert(
        self,
        path: Path,
        *,
        resource_name: str,
        storage,  # openviking_cli.utils.storage storage helper
        format_hint: str | None = None,
    ) -> AnydocConversionResult: ...
```

- 同步实现；由调用方 `asyncio.to_thread` 包装，避免阻塞事件循环。

### 4.2 Document → Markdown（含图片）

使用 `anydoc.to_document(bytes[, format])`，**不要**仅用 `to_markdown`（会丢掉可用图片引用）。

遍历 `document.blocks`（及 list/table/blockquote 嵌套、notes）中的 `Inline`：

| Inline | 输出 |
|--------|------|
| text + style | 按 bold/italic/strike/code 输出 GFM |
| link | `[text](url)` / 相对 / anchor |
| image + `source.kind == asset` | 见下节图片处理 |
| image + `source.kind == external` | `![alt](url)` |
| image + `unavailable` | 仅保留 alt 文本（或跳过空 alt） |
| lineBreak | 换行 |
| noteRef / anchor | 脚注按引用顺序编号；anchor link 解析到 heading slug 或显式 HTML anchor |

表格、列表、标题、代码块、分割线按 anydoc model 语义序列化为 GFM。当前采用自研轻量 renderer，保证图片引用可控，不依赖 anydoc 默认「只输出 alt」行为。

renderer 已覆盖的质量处理：

1. 文本按上下文转义 Markdown 特殊字符，避免原文中的 `#`、`-`、`*`、`[`、反引号、表格竖线等被误识别成 Markdown 结构。
2. 相邻同样式 text run 会先合并，再统一输出 bold/italic/strike/code，减少碎片化样式。
3. inline code 和 code block 会根据内容里的反引号长度选择更长 fence，避免代码内容截断 Markdown。
4. link 支持 external、relative、anchor；URL 中的 `|`、`<`、`>` 会编码，含空格或括号时用 `<...>` 包裹。
5. anchor link 会解析到 heading 的 GFM slug；非 heading anchor 被引用时输出 HTML anchor。
6. list 支持普通项目、数字起始值、alpha/roman marker、marker label、checkbox，以及多 block item 的 loose list。
7. table 支持 AnyDoc 的 `origin` / `covered` slot、`header_rows`、layout 单格表降级、cell 内列表/标题/代码块/嵌套表压平。
8. notes 按正文引用顺序编号，未引用但有内容的 note 追加到末尾，输出标准 footnote definition。
9. PPT 类格式中的 speaker notes 以 `### Speaker Notes` 输出，提升可读性。

### 4.3 图片落盘（对齐现有 Word/PDF）

对 `media_type` 以 `image/` 开头的 asset：

1. `extension`：从 MIME（`image/png` → `.png`）或 `origin_part` 后缀推断；未知则 `.png`。
2. `filename`：`anydoc_asset_{asset_id}`，同一个 AnyDoc asset 多处引用时复用同一落盘路径。
3. 保存前复用共享图片校验 `openviking.parse.image_validation.is_valid_image`，过滤损坏、极小、极端比例或超大图片。
4. `path = storage.save_image(resource_name, bytes, filename=filename, extension=extension)`。
5. Markdown：`![alt_or_filename]({path.relative_to(storage.media_dir)})`。
6. Parser 调用 `MarkdownParser.parse_content(..., allowed_media_dirs=[storage.media_dir])`。

非 `image/*` 的 embedded object：跳过字节落盘，打 debug 日志，不阻断转换。

### 4.4 错误映射

| anydoc 异常 | OV 行为 |
|-------------|---------|
| `EncryptedError` | 抛出带路径的明确错误，不静默空文档 |
| `UnsupportedError` | 同上 |
| `MalformedError` / `MissingPartError` / `ResourceLimitError` | 记录 warning + 明确错误 |
| 其它 | 向上抛出 |

不提供 legacy fallback。anydoc 失败即失败，避免旧路径与新路径行为分叉。

### 4.5 PDF 明确排除

- `AnyDocConverter` 若检测到 PDF（content 或 hint），直接拒绝并提示走 `PDFParser`。
- Registry 不把 `.pdf` 指到 anydoc。

---

## 5. 统一 Parser 接入方式

| Parser | 改动要点 |
|--------|----------|
| `AnyDocParser` | 注册全部 Office/EPUB 扩展名；默认调用 `AnyDocConverter`；再委托 `MarkdownParser`；追加 `storage.media_dir` 到 `allowed_media_dirs` |
| `PDFParser` | **无改动** |

扩展名注册通过单一 `AnyDocParser.supported_extensions` + `ParserRegistry._register("anydoc", ...)` 完成，不引入第三方插件注册。

**扩展名映射**：

| 扩展名 | 挂靠 Parser |
|--------|-------------|
| `.doc` `.docx` `.docm` `.odt` `.rtf` | `AnyDocParser` |
| `.pptx` `.ppt` `.pptm` `.pps` `.ppsx` `.ppsm` `.pot` `.odp` | `AnyDocParser` |
| `.xlsx` `.xls` `.xlsm` `.xlsb` `.ods` `.csv` | `AnyDocParser` |
| `.epub` | `AnyDocParser` |

当 `parsers.anydoc.enable=false` 时，Office/EPUB 解析直接失败；旧 converter 已删除，不会静默降级。

`.xlsb` 保留支持：anydoc 官方支持列表包含该扩展名，并将
`format_from_extension(".xlsb")` 映射到共享的 `"xlsx"` 解析器；已用真实二进制
XLSB 样本验证 `to_document` 可成功转换。

旧 Excel 专用 parser 配置和行数裁剪配置已删除。AnyDoc 默认全量转换；长表格由 `MarkdownParser` 的 row-aware split 避免切断行。

---

## 6. 依赖与配置

### 依赖

- 新增：`firecrawl-anydoc`（PyPI，import 名 `anydoc`）。
- CI/本地需有对应平台 wheel；若安装失败应在测试中 `pytest.importorskip("anydoc")` 仅用于可选环境——**产品路径建议作为硬依赖**，与 pdfplumber 同级。

### 配置（可选，保持最小）

```yaml
parsers:
  anydoc:
    enable: true                 # 总开关；false 时 Office/EPUB 解析失败
```

配置类放在 `parser_config.py`，缺省值保证未配置时行为为「启用 anydoc、不 fallback」。
总开关字段是 `parsers.anydoc.enable`；继承字段
`parsers.anydoc.enabled` 不参与 anydoc 转换开关判断。

---

## 7. 测试计划

| 用例 | 目的 |
|------|------|
| `test_anydoc_converter_rewrites_asset_images` | 构造/fixture document：asset 落盘 + MD 含 `![](resource/images/...)` |
| `test_anydoc_converter_skips_non_image_assets` | 非 image MIME 不落盘 |
| `test_anydoc_converter_rejects_pdf` | PDF 不走 converter |
| `test_anydoc_parser_converts_and_forwards_markdown_options` | AnyDoc 主链路 → MarkdownParser 参数转发、media dir、meta |
| `test_anydoc_parser_default_reraises_conversion_failure` | 默认失败不 fallback |
| 现有 PDF / markdown 测试 | 全绿，证明 PDF 未受影响 |

Fixture：在 `tests/parse/fixtures/anydoc/` 放置最小二进制样例（或测试内动态生成 docx）。注意许可证与体积。

---

## 8. 文档与 PR

### 文档

- `docs/zh/api/02-resources.md` / `docs/en/api/02-resources.md`：更新支持格式表与「基于 anydoc 转换」说明。
- 本文档：`docs/design/anydoc-office-converter.md`。

### PR 标准

1. 单一主题：Office 转换换 anydoc + 图片桥接。
2. 含测试与中英文 API 文档。
3. 不夹杂无关重构；旧 Office/EPUB parser 已在本轮清理。
4. PR 描述写清：PDF 不变、图片如何从 `assets` 接入、AnyDoc 失败行为。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| anydoc 默认 MD 无图片路径 | 强制 `to_document` + OV serializer |
| PDF 无 document model | 明确排除，保留 `PDFParser` |
| Excel 行数控制语义变化 | 旧 Excel 专用配置已删除；通过 Markdown row-aware split 保障不切断表格行 |
| Python 绑定字段名（camelCase vs snake_case） | 实现时对绑定做适配层，单测锁字段访问 |
| wheel / 平台支持 | 硬依赖 + CI 验证；文档注明支持平台 |
| 大文档 assets 内存 | anydoc 自有 `max_asset_total_bytes`；超限按 ConvertError 处理 |
| AGPL 工程依赖 MIT anydoc | 许可兼容 |

---

## 10. 实现顺序（概要）

1. 添加依赖与 `AnyDocConverter`（含图片改写）+ 单测。
2. 新增统一 `AnyDocParser`，打通 `allowed_media_dirs`、`base_dir`、source name 转发和 meta。
3. `ParserRegistry` 将 Office/EPUB 扩展名统一注册到 `AnyDocParser`。
4. 旧 Office/EPUB parser 和旧生产依赖已删除，不保留 legacy fallback。
5. 扩展名与中英文文档。
6. 回归：PDF 与现有 parse 测试。

详细任务拆解见后续 implementation plan（`docs/superpowers/plans/` 或团队约定路径）。

---

## 11. 已拍板决策

1. **替换范围**：Office / EPUB 默认主链路统一到 `AnyDocParser`；**PDF 不换**。
2. **图片**：按 OV 设计从 `document.assets` 落盘并写回 Markdown 引用。
3. **架构**：单一 `AnyDocParser` 编排主链路，共享 `AnyDocConverter`，不保留旧 parser fallback。
4. **交付标准**：可直接开 PR（代码 + 测试 + 文档）。
5. **扩展名**：§5 表格为必须范围（含 ODT/RTF/ODS/ODP/CSV）。
6. **Excel 行数限制**：旧 Excel 专用配置已删除；AnyDoc 全量转换，Markdown 层做行边界切分。
