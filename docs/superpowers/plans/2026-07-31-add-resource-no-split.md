# `add_resource` 不拆分模式实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将错误的 `no_parse` 原文件直存模式改为 `no_split`，保持正常格式解析和 Markdown 转换，但每个文档正文只生成一个 Markdown 文件。

**Architecture:** 对外入口继续传递 `parse_mode`，统一资源处理器把 `no_split` 转换为解析器内部的 `split_content=False`。所有格式 Parser 继续复用现有转换逻辑，最终由 `MarkdownParser` 在布局阶段直接规划唯一的 `<文档名>.md`；目录解析器把该选项传给每个子文档。

**Tech Stack:** Python 3.10+、FastAPI/Pydantic v2、pytest、Rust/clap、Go、TypeScript/Vitest。

## Global Constraints

- `parse_mode` 只接受 `default` 和 `no_split`；不兼容尚未合入的 `no_parse`。
- `default` 的解析后端选择、布局和 URI 行为不变。
- `no_split` 不保留 PDF、DOCX 等源文件字节，仍执行格式转换。
- 单个源文档只生成一个正文 Markdown；被正文引用的图片和必要 sidecar 不受此约束。
- 目录输入中的每个文档分别生成一个 Markdown，不合并多个源文档。
- `preserve_structure` 继续只控制目录层级，与 `no_split` 不冲突。
- 语义理解、`.abstract.md`、`.overview.md`、向量化和内部向量分块不变。
- 外部解析后端无法保证不拆分时必须返回明确错误，不允许静默改后端或事后拼接。
- 默认模式的 HTTP 客户端继续省略 `parse_mode`，兼容旧服务端。
- 更新 PR 前必须 rebase 最新 `upstream/main`、解决冲突并重新验证。

---

### Task 1: Markdown 单文件布局

**Files:**
- Modify: `openviking/parse/mode.py`
- Modify: `openviking/parse/parsers/markdown.py`
- Create: `tests/parse/test_markdown_no_split.py`
- Delete: `openviking/parse/parsers/direct.py`
- Delete: `tests/parse/test_no_parse_stager.py`
- Modify: `openviking/parse/directory_scan.py`

**Interfaces:**
- Produces: `ParseMode.DEFAULT`、`ParseMode.NO_SPLIT`、`normalize_parse_mode(value) -> ParseMode`
- Produces: `MarkdownParser.parse_content(..., split_content: bool = True, **kwargs) -> ParseResult`
- Produces: `MarkdownParser._build_structure(..., split_content: bool = True) -> None`

- [ ] **Step 1: 写枚举和长 Markdown 不拆分失败测试**

```python
def test_normalize_parse_mode_accepts_no_split() -> None:
    assert normalize_parse_mode("no_split") is ParseMode.NO_SPLIT
    with pytest.raises(InvalidArgumentError):
        normalize_parse_mode("no_parse")


@pytest.mark.asyncio
async def test_long_markdown_no_split_writes_one_complete_file(fake_fs) -> None:
    content = "\n\n".join(f"paragraph {index} " + "x" * 500 for index in range(30))
    parser = MarkdownParser(config=ParserConfig(max_section_size=32))
    parser._viking_fs = fake_fs

    result = await parser.parse_content(
        content,
        source_path="/tmp/社交网络中英文剧本.pdf",
        resource_name="社交网络中英文剧本",
        split_content=False,
    )

    body_files = {
        uri: value
        for uri, value in fake_fs.files.items()
        if uri.endswith(".md")
    }
    assert list(body_files) == [
        f"{result.temp_dir_path}/社交网络中英文剧本/社交网络中英文剧本.md"
    ]
    assert next(iter(body_files.values())) == content
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' \
  tests/parse/test_markdown_no_split.py -q
```

Expected: `NO_SPLIT` 不存在，或长文档仍生成多个文件。

- [ ] **Step 3: 实现最小单文件布局**

```python
class ParseMode(str, Enum):
    DEFAULT = "default"
    NO_SPLIT = "no_split"


async def _build_structure(
    self,
    ops,
    content,
    headings,
    root_dir,
    source_path=None,
    doc_name=None,
    split_content=True,
):
    ops.append(_LayoutOp("mkdir", root_dir))
    doc_name = doc_name or self._sanitize_for_path(
        _smart_stem(source_path) if source_path else "content"
    )
    if not split_content:
        ops.append(_LayoutOp("write", f"{root_dir}/{doc_name}.md", content))
        return
    # 保留现有 default 分支
```

`_compute_layout` 从 `kwargs` 读取 `split_content` 并显式传入 `_build_structure`。删除 `DirectResourceStager`，撤销 `scan_directory(..., allow_unsupported=True)` 及仅供直存模式使用的逻辑。

- [ ] **Step 4: 运行 GREEN 和默认拆分回归**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' \
  tests/parse/test_markdown_no_split.py \
  tests/parse/test_markdown_apply_layout.py \
  tests/parse/test_markdown_filename_collision.py -q
```

Expected: 全部通过；默认长文档测试仍产生多个正文文件。

- [ ] **Step 5: 提交核心布局**

```bash
git add openviking/parse/mode.py openviking/parse/parsers/markdown.py \
  openviking/parse/directory_scan.py tests/parse/test_markdown_no_split.py
git add -u openviking/parse/parsers/direct.py tests/parse/test_no_parse_stager.py
git commit -m "feat(resource): support parsed resources without splitting"
```

---

### Task 2: 格式 Parser 与目录传播

**Files:**
- Modify: `openviking/utils/media_processor.py`
- Modify: `openviking/parse/parser_router.py`
- Modify: `openviking/parse/parsers/pdf.py`
- Modify: `openviking/parse/parsers/word.py`
- Modify: `openviking/parse/parsers/directory.py`
- Test: `tests/parse/test_markdown_no_split.py`
- Test: `tests/parse/test_add_directory.py`

**Interfaces:**
- Consumes: `ParseMode.NO_SPLIT`
- Consumes: `split_content: bool`
- Produces: `UnifiedResourceProcessor.process(..., parse_mode=...)` 正常解析并向 Parser 传递 `split_content`
- Produces: `DirectoryParser._process_single_file(..., split_content: bool = True)` 向子 Parser 传递选项

- [ ] **Step 1: 写 PDF 转换和目录逐文档失败测试**

```python
@pytest.mark.asyncio
async def test_pdf_no_split_converts_to_one_markdown(monkeypatch, tmp_path, fake_fs):
    pdf = tmp_path / "社交网络中英文剧本.pdf"
    pdf.write_bytes(b"%PDF-fixture")
    parser = PDFParser()
    parser._markdown_parser = MarkdownParser(config=ParserConfig(max_section_size=32))
    parser._markdown_parser._viking_fs = fake_fs
    monkeypatch.setattr(
        parser,
        "_convert_to_markdown",
        AsyncMock(return_value=("# 剧本\n\n" + "正文\n\n" * 100, {})),
    )

    result = await parser.parse(
        pdf,
        resource_name="社交网络中英文剧本",
        split_content=False,
    )

    assert list(uri for uri in fake_fs.files if uri.endswith(".md")) == [
        f"{result.temp_dir_path}/社交网络中英文剧本/社交网络中英文剧本.md"
    ]
    assert not any(uri.endswith(".pdf") for uri in fake_fs.files)
```

目录测试使用两个长文档夹具，断言每个源文档只生成一个 `.md`，并保留相对父目录。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' \
  tests/parse/test_markdown_no_split.py \
  tests/parse/test_add_directory.py -q
```

Expected: PDF 或目录子 Parser 丢失 `split_content=False`，测试失败。

- [ ] **Step 3: 实现处理器与 Parser 传播**

```python
mode = normalize_parse_mode(parse_mode)
parse_kwargs["split_content"] = mode is ParseMode.DEFAULT
```

移除 `NO_PARSE` 的原内容字符串、`preserve_structure` 和直存分支。PDF、Word 等没有完整转发 `**kwargs` 的包装 Parser 显式传递：

```python
split_content=kwargs.get("split_content", True)
```

目录解析器从 `parse()` 读取 `split_content`，传入 `_process_single_file`，再传给 `parser.parse(...)`。

ParserRouter 在选择 Understanding 后端且 `split_content=False` 时抛出：

```python
raise InvalidArgumentError(
    "parse_mode='no_split' is not supported by the configured Understanding parser."
)
```

- [ ] **Step 4: 运行 GREEN 和格式解析回归**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' \
  tests/parse/test_markdown_no_split.py \
  tests/parse/test_add_directory.py \
  tests/parse/test_directory_parser_routing.py \
  tests/parse/test_document_parser_threading.py -q
```

Expected: 全部通过。

- [ ] **Step 5: 提交 Parser 传播**

```bash
git add openviking/utils/media_processor.py openviking/parse/parser_router.py \
  openviking/parse/parsers/pdf.py openviking/parse/parsers/word.py \
  openviking/parse/parsers/directory.py \
  tests/parse/test_markdown_no_split.py tests/parse/test_add_directory.py
git commit -m "feat(parser): keep converted markdown in one file"
```

---

### Task 3: 服务端入口和后台重放

**Files:**
- Modify: `openviking/service/resource_service.py`
- Modify: `openviking/connector/delegate.py`
- Modify: `openviking/server/routers/resources.py`
- Modify: `openviking/server/mcp_endpoint.py`
- Modify: `openviking/server/resource_ingest.py`
- Modify: `openviking/server/upload_token_store.py`
- Modify: `openviking/storage/queuefs/add_resource_msg.py`
- Test: `tests/service/test_resource_service_parse_mode.py`
- Test: `tests/service/test_resource_service_connector.py`
- Test: `tests/server/test_resource_parse_mode_request.py`
- Test: `tests/server/test_resource_ingest_parse_mode.py`
- Test: `tests/server/test_mcp_parse_mode.py`
- Test: `tests/server/test_upload_token_store.py`

**Interfaces:**
- Consumes: `ParseMode.DEFAULT | ParseMode.NO_SPLIT`
- Produces: REST/MCP/上传/队列/监听任务一致持久化字符串 `"no_split"`

- [ ] **Step 1: 把入口测试改成 `no_split` 并增加目录展平允许测试**

```python
def test_add_resource_request_accepts_no_split_with_flat_directory() -> None:
    request = AddResourceRequest(
        path="https://example.com/docs.zip",
        parse_mode="no_split",
        preserve_structure=False,
    )
    assert request.parse_mode is ParseMode.NO_SPLIT


@pytest.mark.asyncio
async def test_no_split_is_forwarded_and_persisted_for_watch_replay(service, ctx):
    await service.add_resource(
        path="/test/path",
        ctx=ctx,
        to="viking://resources/no_split_watch",
        watch_interval=30,
        parse_mode="no_split",
    )
    assert service._resource_processor.calls[-1]["parse_mode"] == "no_split"
```

所有原 `no_parse` 正向断言改成 `no_split`；增加 `no_parse` 被拒绝测试。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' \
  tests/service/test_resource_service_parse_mode.py \
  tests/service/test_resource_service_connector.py \
  tests/server/test_resource_parse_mode_request.py \
  tests/server/test_resource_ingest_parse_mode.py \
  tests/server/test_mcp_parse_mode.py \
  tests/server/test_upload_token_store.py -q
```

Expected: 现有枚举仍为 `no_parse`，或仍拒绝 `preserve_structure=false`。

- [ ] **Step 3: 修改服务实现**

删除所有：

```python
if mode is ParseMode.NO_PARSE and preserve_structure is False:
    raise InvalidArgumentError(...)
```

把所有非默认模式判断改为 `ParseMode.NO_SPLIT`。服务端不绕过标准解析链；异步 Understanding 快捷提交仍仅用于 `default`，让 `no_split` 在 ParserRouter 得到明确的不支持错误。

连接器、上传 token、AddResourceMsg、监听任务继续按原通道传递 `mode.value`，仅把值改成 `no_split`。

- [ ] **Step 4: 运行 GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' \
  tests/service/test_resource_service_parse_mode.py \
  tests/service/test_resource_service_connector.py \
  tests/server/test_resource_parse_mode_request.py \
  tests/server/test_resource_ingest_parse_mode.py \
  tests/server/test_mcp_parse_mode.py \
  tests/server/test_upload_token_store.py -q
```

Expected: 全部通过。

- [ ] **Step 5: 提交服务端改名**

```bash
git add openviking/service/resource_service.py openviking/connector/delegate.py \
  openviking/server/routers/resources.py openviking/server/mcp_endpoint.py \
  openviking/server/resource_ingest.py openviking/server/upload_token_store.py \
  openviking/storage/queuefs/add_resource_msg.py \
  tests/service/test_resource_service_parse_mode.py \
  tests/service/test_resource_service_connector.py \
  tests/server/test_resource_parse_mode_request.py \
  tests/server/test_resource_ingest_parse_mode.py \
  tests/server/test_mcp_parse_mode.py tests/server/test_upload_token_store.py
git commit -m "refactor(resource): rename parse mode to no-split"
```

---

### Task 4: Python 客户端与 SDK

**Files:**
- Modify: `openviking/__init__.py`
- Modify: `openviking/async_client.py`
- Modify: `openviking/client/local.py`
- Modify: `openviking/sync_client.py`
- Modify: `openviking_cli/client/base.py`
- Modify: `sdk/python/openviking_sdk/__init__.py`
- Modify: `sdk/python/openviking_sdk/client.py`
- Test: `tests/client/test_resource_parse_mode_clients.py`
- Test: `tests/client/test_http_client_compact.py`

**Interfaces:**
- Produces: Python 客户端 `parse_mode="no_split"` 请求字段
- Produces: 默认模式仍不发送 `parse_mode`

- [ ] **Step 1: 修改客户端测试并确认 RED**

```python
assert request_payload["parse_mode"] == "no_split"
with pytest.raises(InvalidArgumentError):
    await client.add_resource("a.pdf", parse_mode="no_parse")
```

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' \
  tests/client/test_resource_parse_mode_clients.py \
  tests/client/test_http_client_compact.py -q
```

Expected: `no_split` 尚未被现有客户端枚举接受。

- [ ] **Step 2: 修改类型、透传和紧凑请求**

所有公开类型、文档字符串和测试夹具把 `"no_parse"` 改为 `"no_split"`。默认值仍为 `ParseMode.DEFAULT`，HTTP 紧凑请求继续删除 `"parse_mode": "default"`。

- [ ] **Step 3: 运行 GREEN 并提交**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' \
  tests/client/test_resource_parse_mode_clients.py \
  tests/client/test_http_client_compact.py -q
```

Then:

```bash
git add openviking/__init__.py openviking/async_client.py \
  openviking/client/local.py openviking/sync_client.py \
  openviking_cli/client/base.py sdk/python/openviking_sdk/__init__.py \
  sdk/python/openviking_sdk/client.py \
  tests/client/test_resource_parse_mode_clients.py \
  tests/client/test_http_client_compact.py
git commit -m "feat(sdk): expose no-split parse mode"
```

---

### Task 5: Rust、Go 和 TypeScript 客户端

**Files:**
- Modify: `crates/ov_cli/src/client.rs`
- Modify: `crates/ov_cli/src/main.rs`
- Modify: `sdk/go/resources.go`
- Modify: `sdk/go/types.go`
- Modify: `sdk/go/client_test.go`
- Modify: `sdk/typescript/src/types.ts`
- Modify: `sdk/typescript/tests/client.test.ts`

**Interfaces:**
- Produces: Rust CLI `--parse-mode no_split`
- Produces: Go `ParseModeNoSplit`
- Produces: TypeScript `parseMode?: "default" | "no_split"`

- [ ] **Step 1: 修改各语言失败测试**

Rust：

```rust
assert_eq!(parse_mode, ParseMode::NoSplit);
assert!(Cli::try_parse_from([
    "ov", "add-resource", "./a.pdf", "--parse-mode", "no_parse"
]).is_err());
```

Go 和 TypeScript 请求断言 JSON 字段为 `"no_split"`。

- [ ] **Step 2: 运行并确认 RED**

Run:

```bash
cargo test -p ov_cli parse_mode -- --nocapture
go test ./sdk/go/...
cd sdk/typescript && npm test -- --run
```

Expected: 旧枚举和值导致失败。

- [ ] **Step 3: 修改实现和公开类型**

Rust：

```rust
enum ParseMode {
    Default,
    #[value(name = "no_split", alias = "no-split")]
    NoSplit,
}
```

Go：

```go
const ParseModeNoSplit ParseMode = "no_split"
```

TypeScript：

```typescript
parseMode?: "default" | "no_split";
```

- [ ] **Step 4: 格式化、运行 GREEN 并提交**

Run:

```bash
cargo fmt --all -- --check
cargo test -p ov_cli parse_mode -- --nocapture
gofmt -w sdk/go/resources.go sdk/go/types.go sdk/go/client_test.go
go test ./sdk/go/...
cd sdk/typescript && npm test -- --run
```

Then:

```bash
git add crates/ov_cli/src/client.rs crates/ov_cli/src/main.rs \
  sdk/go/resources.go sdk/go/types.go sdk/go/client_test.go \
  sdk/typescript/src/types.ts sdk/typescript/tests/client.test.ts
git commit -m "feat(cli): add no-split resource mode"
```

---

### Task 6: 文档和残留清理

**Files:**
- Modify: `docs/en/api/02-resources.md`
- Modify: `docs/zh/api/02-resources.md`
- Modify: `docs/en/guides/06-mcp-integration.md`
- Modify: `docs/zh/guides/06-mcp-integration.md`

**Interfaces:**
- Produces: 用户文档只描述 `default | no_split`

- [ ] **Step 1: 更新 API、MCP 与 CLI 示例**

中文说明统一为：

```text
no_split：仍调用格式解析器并转换为 Markdown，但每个源文档正文只保存为一个 Markdown 文件。
```

英文说明统一为：

```text
no_split still runs format parsing and Markdown conversion, but stores each source document body as one Markdown file.
```

- [ ] **Step 2: 扫描错误语义残留**

Run:

```bash
rg -n "no_parse|NO_PARSE|NoParse|no-parse|DirectResourceStager" \
  crates openviking openviking_cli sdk tests docs/en docs/zh
```

Expected: 无匹配。设计文档和 Git 历史不在该扫描范围。

- [ ] **Step 3: 差异检查并提交**

Run:

```bash
git diff --check
git status --short
```

Then:

```bash
git add docs/en/api/02-resources.md docs/zh/api/02-resources.md \
  docs/en/guides/06-mcp-integration.md docs/zh/guides/06-mcp-integration.md
git commit -m "docs(resource): document no-split parsing"
```

---

### Task 7: 全量验证、rebase 和 PR 更新

**Files:**
- Verify only; conflict resolution may modify files touched by upstream.

**Interfaces:**
- Produces: 基于最新 `upstream/main` 的无冲突分支
- Produces: 现有 PR 的中文标题与中文描述

- [ ] **Step 1: 运行 Python 相关回归**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' \
  tests/parse/test_markdown_no_split.py \
  tests/parse/test_markdown_apply_layout.py \
  tests/parse/test_markdown_filename_collision.py \
  tests/parse/test_add_directory.py \
  tests/parse/test_directory_parser_routing.py \
  tests/parse/test_document_parser_threading.py \
  tests/client/test_resource_parse_mode_clients.py \
  tests/client/test_http_client_compact.py \
  tests/service/test_resource_service_parse_mode.py \
  tests/service/test_resource_service_connector.py \
  tests/server/test_resource_parse_mode_request.py \
  tests/server/test_resource_ingest_parse_mode.py \
  tests/server/test_mcp_parse_mode.py \
  tests/server/test_upload_token_store.py -q
```

Expected: 全部通过。

- [ ] **Step 2: 运行 Rust、Go、TypeScript 回归**

Run:

```bash
cargo fmt --all -- --check
cargo test -p ov_cli parse_mode -- --nocapture
gofmt -d sdk/go/resources.go sdk/go/types.go sdk/go/client_test.go
go test ./sdk/go/...
cd sdk/typescript && npm test -- --run
```

Expected: 全部通过，格式化无差异。

- [ ] **Step 3: 获取并 rebase 最新主线**

Run:

```bash
git fetch upstream main
git rebase upstream/main
```

逐个解决冲突后：

```bash
git diff --name-only --diff-filter=U
# 使用 apply_patch 修复上一个命令列出的全部冲突文件
git add -u
GIT_EDITOR=true git rebase --continue
```

Expected: `git merge-base HEAD upstream/main` 等于 `git rev-parse upstream/main`。

- [ ] **Step 4: rebase 后重新运行 Task 7 Step 1 和 Step 2**

Expected: 所有测试再次通过；`git diff --check upstream/main...HEAD` 无错误。

- [ ] **Step 5: 安全强推并更新中文 PR**

Run:

```bash
git push --force-with-lease origin codex/feature-add-resource-no-parse
gh pr edit 3645 \
  --title "feat(resource): 支持解析后不拆分文档" \
  --body-file /tmp/openviking-pr-3645-zh.md
```

PR 描述必须包含：背景、行为变化、接口示例、测试结果、rebase 基线；不得使用英文段落。

- [ ] **Step 6: 验证远端 PR 状态**

Run:

```bash
gh pr view 3645 --json url,title,body,headRefOid,mergeable,mergeStateStatus,statusCheckRollup
git status --short --branch
```

Expected: PR 标题和正文为中文，`headRefOid` 等于本地 `HEAD`，`mergeable` 不是 `CONFLICTING`，工作区干净。
