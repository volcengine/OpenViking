# Custom Parser Plugin Example

这个目录演示如何为 OpenViking 编写和注册自定义 parser。

当前包含两个示例：

- `custom_txt_parser.py`
  演示如何接管 `.txt` / `.text` 文件，并在解析前做自定义预处理。
- `custom_jsonl_parser.py`
  演示如何接管 `.jsonl` 文件，把每一行的 `title` / `content` 记录转换成 Markdown，再复用内置 `MarkdownParser`。

## 目录结构

```text
examples/custom-parser-plugin/
├── README.md
├── custom_jsonl_parser.py
├── custom_txt_parser.py
├── example-dir/
│   ├── new_supported_jsonl.jsonl
│   └── pure_text_file.txt
└── ov.conf
```

## JSONL 示例说明

`custom_jsonl_parser.py` 只支持示例格式，不追求通用 JSONL 兼容性。

输入文件格式：

```jsonl
{"title": "test title1", "content": "test content1"}
{"title": "test title2", "content": "test content2"}
```

对应示例文件：

- `example-dir/new_supported_jsonl.jsonl`

解析逻辑很简单：

1. 逐行读取 JSONL
2. 每一行必须是 JSON object
3. 每一行必须包含 `title` 和 `content`
4. 转换成下面这种 Markdown

```md
# test title1

test content1

# test title2

test content2
```

5. 把转换后的 Markdown 交给 `MarkdownParser`

这样做的好处是示例代码足够短，同时又能直接复用 OpenViking 现有的 Markdown 解析能力。

## `MyCustomJsonlParser` 核心代码

```python
class MyCustomJsonlParser(BaseParser):
    @property
    def supported_extensions(self) -> List[str]:
        return [".jsonl"]

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        markdown_content = self._jsonl_to_markdown(content)
        result = await self._md_parser.parse_content(
            markdown_content,
            source_path=source_path,
            instruction=instruction,
            **kwargs,
        )
        result.source_format = "jsonl"
        result.parser_name = "MyCustomJsonlParser"
        return result
```

## 配置方式

OpenViking 支持在 `ov.conf` 中注册自定义 parser：

```json
{
  "custom_parsers": {
    "my-jsonl-parser": {
      "class": "your_package.custom_jsonl_parser.MyCustomJsonlParser",
      "extensions": [".jsonl"]
    }
  }
}
```

`class` 必须指向一个可以被 Python `import` 的类路径。

## 重要说明

当前示例目录名是 `custom-parser-plugin`，目录名里有 `-`。这意味着：

- `examples.custom-parser-plugin.custom_jsonl_parser.MyCustomJsonlParser`
- `examples.custom-parser-plugin.custom_txt_parser.MyCustomTxtParser`

这两种写法都不能作为真实的 Python import 路径直接使用。

如果你想把这里的示例真正注册到 `ov.conf` 中运行，应该把 parser 文件放到一个可导入的模块路径下，例如：

```text
my_parsers/
├── __init__.py
├── custom_jsonl_parser.py
└── custom_txt_parser.py
```

然后在配置中写：

```json
{
  "custom_parsers": {
    "my-jsonl-parser": {
      "class": "my_parsers.custom_jsonl_parser.MyCustomJsonlParser",
      "extensions": [".jsonl"]
    },
    "my-txt-parser": {
      "class": "my_parsers.custom_txt_parser.MyCustomTxtParser",
      "extensions": [".txt", ".text"],
      "kwargs": {
        "plugin_name": "my-txt-parser",
        "version": "1.0"
      }
    }
  }
}
```

## 本地验证建议

如果你只是想快速理解这个 JSONL 示例，先看这两个文件即可：

- `custom_jsonl_parser.py`
- `example-dir/new_supported_jsonl.jsonl`

如果你要在自己的项目中使用：

1. 把 parser 文件移动到可导入的 Python package
2. 修改 `ov.conf` 里的 `class`
3. 为目标扩展名写入 `custom_parsers`
4. 启动 OpenViking server，确认自定义 parser 已覆盖对应扩展名

## 相关文件

- `custom_jsonl_parser.py`
- `custom_txt_parser.py`
- `ov.conf`
- `example-dir/new_supported_jsonl.jsonl`
