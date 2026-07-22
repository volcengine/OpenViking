# ov compile One-Page demo

这是关于 ov compile 的测试demo。

## 1. 启动服务与 CLI

```bash
.venv/bin/openviking-server --with-bot
```

在另一个终端执行：

```bash
cargo build -p ov_cli
OV=target/debug/ov
```

## 2. 添加 Skill 和材料

```bash
$OV add-skill \
  examples/ov-compile-one-page-demo/skills/build-one-page-index \
  --wait

$OV add-resource \
  examples/ov-compile-one-page-demo/resources \
  --to viking://resources/knowledge-parsing-one-page-source \
  --wait
```

使用下面的命令查看 Skill 实际安装位置：

```bash
$OV skills show build-one-page-index --format json
```

## 3. 准备目标目录

```bash
$OV mkdir viking://resources/knowledge-parsing-one-page-wiki
```

## 4. 编译 One-Page

将 `<SKILL_URI>` 替换为上一步返回的 Skill root URI 或 `SKILL.md` URI。

```bash
$OV compile \
  --from viking://resources/knowledge-parsing-one-page-source \
  --to viking://resources/knowledge-parsing-one-page-wiki \
  --skill <SKILL_URI> \
  --reason "把这批企业知识库文档解析材料整理成一个面向产品、研发和运维的 One-Page 知识导航页" \
  --wait \
  --timeout 1800
```

## 5. 检查结果

```bash
$OV tree viking://resources/knowledge-parsing-one-page-wiki
```

