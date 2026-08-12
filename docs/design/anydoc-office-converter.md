# anydoc Office 转换层设计

**范围**: 用 [firecrawl/anydoc](https://github.com/firecrawl/anydoc) 替换 OpenViking 内置 Office/EPUB 的「格式 → Markdown」转换层，并按现有媒体链路补齐嵌入图片落盘与 Markdown 引用。
**状态**: 已实现。
**日期**: 2026-08-12

---

## 1. 背景与问题

当前 Office 解析（Word / PowerPoint / Excel / Legacy `.doc` / EPUB）多为 markitdown 风格的自研转换：依赖 `python-docx`、`python-pptx`、`openpyxl`、`ebooklib`、`olefile` 等，再交给 `MarkdownParser` 做结构切分与落盘。

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
5. 以可合并 PR 的标准交付：聚焦改动、测试覆盖、文档同步、行为可回退。

### 非目标

1. 不替换 `PDFParser`、MinerU、Understanding、HTML/网页、飞书、代码仓、音视频解析。
2. 不重写 `MarkdownParser` 切分/落盘逻辑。
3. 不接入 Firecrawl 托管 OCR / Parse API。
4. 首 PR 不强制删除 `python-docx` / `python-pptx` / `openpyxl` / `ebooklib` / `olefile`（可作为失败 fallback；清理依赖可跟后续 PR）。

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
  Word/PPT/Excel/   PDFParser
  LegacyDoc/EPub    (不变)
     │
     ▼
 AnyDocConverter.convert(path, resource_name, storage)
   ├─ read bytes
   ├─ anydoc.to_document(bytes[, format])
   ├─ serialize Document → GFM
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

核心新增模块：`openviking/parse/parsers/anydoc_converter.py`。

职责边界：

| 组件 | 负责 | 不负责 |
|------|------|--------|
| `AnyDocConverter` | anydoc 调用、错误映射、document→GFM、图片落盘与引用改写 | 切分、VikingFS、向量化 |
| 各 `*Parser` | 扩展名注册、`parse()` 编排、委托 `MarkdownParser` | 具体 Office 字节解析 |
| `MarkdownParser` | 结构切分、本地图片摄入、temp 树 | Office 格式理解 |
| `PDFParser` | 本地/远程 PDF（含抽图） | — |

---

## 4. AnyDocConverter 设计

### 4.1 公共 API（建议）

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

- 同步实现；由调用方 `asyncio.to_thread` 包装（与现有 Word/PPT 一致）。
- Excel 进程池 worker 内直接调用同步 `convert`，避免在子进程再开事件循环做转换。

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
| noteRef / anchor | 保持可读的最小表示（脚注可附文末 notes） |

表格、列表、标题、代码块、分割线按 anydoc model 语义序列化为 GFM。实现上可：

1. **优先**：自研轻量 serializer（只覆盖 OV 需要的块类型），图片处插入本地路径；或
2. **备选**：若 Python 绑定后续提供「带 asset URL 映射的 serialize」再切换。

首 PR 采用 (1)，保证图片引用可控，不依赖 anydoc 默认「只输出 alt」行为。

### 4.3 图片落盘（对齐现有 Word/PDF）

对 `media_type` 以 `image/` 开头的 asset：

1. `extension`：从 MIME（`image/png` → `.png`）或 `origin_part` 后缀推断；未知则 `.png`。
2. `filename`：`image{n}`，`n` 从 1 递增（与 `WordParser` 一致）。
3. `path = storage.save_image(resource_name, bytes, filename=filename, extension=extension)`。
4. Markdown：`![alt_or_filename]({path.relative_to(storage.media_dir)})`。
5. Parser 调用 `MarkdownParser.parse_content(..., allowed_media_dirs=[storage.media_dir])`。

非 `image/*` 的 embedded object：跳过字节落盘，打 debug 日志，不阻断转换。

### 4.4 错误映射

| anydoc 异常 | OV 行为 |
|-------------|---------|
| `EncryptedError` | 抛出带路径的明确错误，不静默空文档 |
| `UnsupportedError` | 同上；若启用 fallback 则降级旧转换器 |
| `MalformedError` / `MissingPartError` / `ResourceLimitError` | 记录 warning + 明确错误；可选 fallback |
| 其它 | 向上抛出 |

**Fallback（建议默认关闭）**：配置项例如 `parsers.anydoc.fallback_to_legacy: false`。为 true 时，anydoc 失败可回退现有 `_convert_to_markdown` 实现。首 PR 可保留旧代码路径供 fallback/对比，但不默认启用。

### 4.5 PDF 明确排除

- `AnyDocConverter` 若检测到 PDF（content 或 hint），直接拒绝并提示走 `PDFParser`。
- Registry 不把 `.pdf` 指到 anydoc。

---

## 5. 各 Parser 接入方式

| Parser | 改动要点 |
|--------|----------|
| `WordParser` | `_convert_to_markdown` → `AnyDocConverter`；`supported_extensions` 增加 `.docm`；保留 `allowed_media_dirs` |
| `LegacyDocParser` | 真实 OLE `.doc` 走 AnyDoc；ZIP/OOXML 伪装 `.doc` **仍**路由到 `WordParser`（现有安全逻辑保留） |
| `PowerPointParser` | 转换改 AnyDoc；扩展名增加 `.ppt`、`.pptm`、`.pps`、`.ppsx`、`.ppsm`、`.pot` 等 anydoc 支持且合理的集合 |
| `ExcelParser` | 转换改 AnyDoc；进程池 worker 内改用 `AnyDocConverter`；扩展名增加 `.xlsb`、`.ods`、`.csv`；`max_rows_per_sheet` 见下 |
| `EPubParser` | 转换改 AnyDoc |
| `PDFParser` | **无改动** |

扩展名注册仍通过各 Parser 的 `supported_extensions` + `ParserRegistry._register`，不引入第三方插件注册。

**扩展名映射（首 PR 必须做完）**：

| 扩展名 | 挂靠 Parser |
|--------|-------------|
| `.docx` `.docm` `.odt` `.rtf` | `WordParser` |
| `.doc` | `LegacyDocParser`（OLE）；OOXML 伪装仍转 `WordParser` |
| `.pptx` `.ppt` `.pptm` `.pps` `.ppsx` `.ppsm` `.pot` `.odp` | `PowerPointParser` |
| `.xlsx` `.xls` `.xlsm` `.xlsb` `.ods` `.csv` | `ExcelParser` |
| `.epub` | `EPubParser` |

**`max_rows_per_sheet`（拍板）**：anydoc 路径下该配置仍保留，但首 PR **不保证**与 openpyxl 路径逐行等价裁剪。行为定为：默认全量转换；若 `max_rows_per_sheet > 0`，在转换后的 Markdown 上按「每个 sheet 二级标题下的第一个表格」做行截断（表头保留 + 最多 N 行数据），并在截断处追加一行说明注释。做不到稳定识别 sheet 边界时，打 warning 并跳过截断（不静默丢数据）。

---

## 6. 依赖与配置

### 依赖

- 新增：`firecrawl-anydoc`（PyPI，import 名 `anydoc`）。
- CI/本地需有对应平台 wheel；若安装失败应在测试中 `pytest.importorskip("anydoc")` 仅用于可选环境——**产品路径建议作为硬依赖**，与 pdfplumber 同级。

### 配置（可选，保持最小）

```yaml
parsers:
  anydoc:
    enable: true                 # 总开关；false 时走 legacy 转换
    fallback_to_legacy: false    # anydoc 失败是否降级
```

配置类放在 `parser_config.py`，缺省值保证未配置时行为为「启用 anydoc、不 fallback」。

---

## 7. 测试计划

| 用例 | 目的 |
|------|------|
| `test_anydoc_converter_rewrites_asset_images` | 构造/fixture document：asset 落盘 + MD 含 `![](resource/images/...)` |
| `test_anydoc_converter_skips_non_image_assets` | 非 image MIME 不落盘 |
| `test_anydoc_converter_rejects_pdf` | PDF 不走 converter |
| `test_word_parser_anydoc_with_image` | 小 docx fixture 含图 → parse 后 media 存在且可被 Markdown 摄入 |
| `test_powerpoint_parser_anydoc` | pptx 烟雾 |
| `test_excel_parser_anydoc_and_process_pool` | 转换路径 + 进程池不回归 |
| `test_legacy_doc_zip_still_routes_to_word` | ZIP 伪装 `.doc` 路由不变 |
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
3. 不夹杂无关重构；legacy 删除另开 PR。
4. PR 描述写清：PDF 不变、图片如何从 `assets` 接入、fallback 默认行为。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| anydoc 默认 MD 无图片路径 | 强制 `to_document` + OV serializer |
| PDF 无 document model | 明确排除，保留 `PDFParser` |
| Excel `max_rows_per_sheet` 语义变化 | 按 §5 拍板：MD 级 best-effort 截断；单测覆盖「可识别 sheet 标题」场景 |
| Python 绑定字段名（camelCase vs snake_case） | 实现时对绑定做适配层，单测锁字段访问 |
| wheel / 平台支持 | 硬依赖 + CI 验证；文档注明支持平台 |
| 大文档 assets 内存 | anydoc 自有 `max_asset_total_bytes`；超限按 ConvertError 处理 |
| AGPL 工程依赖 MIT anydoc | 许可兼容 |

---

## 10. 实现顺序（概要）

1. 添加依赖与 `AnyDocConverter`（含图片改写）+ 单测。
2. 接入 `WordParser` / `LegacyDocParser`，打通 `allowed_media_dirs`。
3. 接入 `PowerPointParser` / `EPubParser`。
4. 接入 `ExcelParser`（含进程池 worker）。
5. 扩展名与中英文文档。
6. 回归：PDF 与现有 parse 测试。

详细任务拆解见后续 implementation plan（`docs/superpowers/plans/` 或团队约定路径）。

---

## 11. 已拍板决策

1. **替换范围**：Word / LegacyDoc / PowerPoint / Excel / EPUB 的转换层；**PDF 不换**。
2. **图片**：按 OV 设计从 `document.assets` 落盘并写回 Markdown 引用。
3. **架构**：共享 `AnyDocConverter`，不在各 Parser 内复制 anydoc 调用。
4. **交付标准**：可直接开 PR（代码 + 测试 + 文档）。
5. **扩展名**：§5 表格为必须范围（含 ODT/RTF/ODS/ODP/CSV）。
6. **Excel 行数限制**：anydoc 路径用 Markdown 级 best-effort 截断，不强制与 openpyxl 逐行一致。
