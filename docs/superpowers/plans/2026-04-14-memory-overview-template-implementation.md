# Memory Overview 模板生成机制实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现基于yaml配置的overview自动生成机制，替换原有的LLM决定机制

**Architecture:** 在memory_updater.py中实现generate_overview方法，当增删改操作完成后，遍历目录下所有memory文件，使用Jinja2模板渲染生成overview文件

**Tech Stack:** Python, Jinja2, Pydantic, VikingFS

---

### Task 1: 在MemoryTypeSchema中添加overview_template字段

**Files:**
- Modify: `openviking/session/memory/dataclass.py:47-62`

- [ ] **Step 1: 添加overview_template字段到MemoryTypeSchema**

在 `operation_mode` 字段后添加：

```python
    overview_template: Optional[str] = Field(
        None, description="Overview template for auto-generating .overview.md files"
    )
```

- [ ] **Step 2: 运行测试验证**

Run: `python -c "from openviking.session.memory.dataclass import MemoryTypeSchema; print('OK')"`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add openviking/session/memory/dataclass.py
git commit -m "feat(memory): add overview_template field to MemoryTypeSchema"
```

---

### Task 2: 在memory_type_registry.py中解析overview_template字段

**Files:**
- Modify: `openviking/session/memory/memory_type_registry.py:165-189`

- [ ] **Step 1: 在_parse_memory_type方法中添加overview_template解析**

在 `operation_mode` 解析后添加：

```python
        overview_template=data.get("overview_template"),
```

完整代码片段（添加到第189行之前）：

```python
        return MemoryTypeSchema(
            memory_type=data.get("memory_type", data.get("name", "")),
            description=data.get("description", ""),
            fields=fields,
            filename_template=data.get("filename_template", ""),
            content_template=data.get("content_template"),
            directory=data.get("directory", ""),
            enabled=data.get("enabled", data.get("enable", True)),
            operation_mode=data.get("operation_mode", "upsert"),
            overview_template=data.get("overview_template"),
        )
```

- [ ] **Step 2: 运行测试验证**

Run: `python -c "from openviking.session.memory.memory_type_registry import MemoryTypeRegistry; r = MemoryTypeRegistry(); print(r.get('events').overview_template if r.get('events') else 'None')"`
Expected: PASS (可能输出None，因为events.yaml还没配置)

- [ ] **Step 3: Commit**

```bash
git add openviking/session/memory/memory_type_registry.py
git commit -m "feat(memory): parse overview_template from YAML config"
```

---

### Task 3: 在memory_updater.py中实现generate_overview方法

**Files:**
- Modify: `openviking/session/memory/memory_updater.py`

- [ ] **Step 1: 添加generate_overview方法**

在 `MemoryUpdater` 类中添加新方法（放在 `_apply_edit_overview` 方法之后）：

```python
    async def generate_overview(
        self,
        memory_type: str,
        directory: str,
        ctx: RequestContext,
    ) -> None:
        """
        Generate .overview.md file for a directory based on overview_template.

        Args:
            memory_type: Memory type name (e.g., 'events')
            directory: Directory path containing memory files
            ctx: Request context
        """
        import jinja2
        from openviking.session.memory.utils.content import parse_memory_file_with_fields

        # Get the schema for this memory type
        registry = self._registry
        schema = registry.get(memory_type)
        if not schema or not schema.overview_template:
            logger.debug(f"No overview_template for memory type: {memory_type}")
            return

        # List all .md files in the directory (excluding .overview.md and .abstract.md)
        try:
            files = await self.viking_fs.list_files(directory, ctx=ctx)
        except Exception as e:
            logger.warning(f"Failed to list files in {directory}: {e}")
            return

        md_files = [
            f for f in files
            if f.endswith(".md") and not f.endswith(".overview.md") and not f.endswith(".abstract.md")
        ]

        if not md_files:
            logger.debug(f"No memory files in {directory}, skipping overview generation")
            return

        # Parse each file and collect items
        items = []
        for file_path in md_files:
            try:
                content = await self.viking_fs.read_file(file_path, ctx=ctx)
                parsed = parse_memory_file_with_fields(content)

                # Extract filename from path
                filename = file_path.split("/")[-1]

                items.append({
                    "file_name": filename,
                    "file_content": parsed,
                })
            except Exception as e:
                logger.warning(f"Failed to parse {file_path}: {e}")
                continue

        if not items:
            logger.debug(f"No valid memory files parsed in {directory}")
            return

        # Render the template
        try:
            env = jinja2.Environment(autoescape=False)
            template = env.from_string(schema.overview_template)
            rendered = template.render(
                memory_type=memory_type,
                items=items,
            )
        except Exception as e:
            logger.error(f"Failed to render overview template for {memory_type}: {e}")
            return

        # Write .overview.md to the directory
        overview_path = f"{directory.rstrip('/')}/.overview.md"
        try:
            await self.viking_fs.write_file(overview_path, rendered, ctx=ctx)
            logger.info(f"Generated overview: {overview_path}")
        except Exception as e:
            logger.error(f"Failed to write overview {overview_path}: {e}")
```

- [ ] **Step 2: 在apply_operations方法末尾添加generate_overview调用**

在 `apply_operations` 方法的末尾（返回之前），添加：

```python
        # Generate overview files if overview_template is configured
        for schema in registry.list_all():
            if schema.overview_template and schema.directory:
                # Render directory path
                env = jinja2.Environment(autoescape=False)
                directory = env.from_string(schema.directory).render(
                    user_space=ctx.user.user_space_name(),
                    agent_space=ctx.user.agent_space_name(),
                )
                await self.generate_overview(schema.memory_type, directory, ctx)
```

需要在文件顶部添加import：
```python
import jinja2
```

- [ ] **Step 3: 运行测试验证**

Run: `python -c "from openviking.session.memory.memory_updater import MemoryUpdater; print('OK')"`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add openviking/session/memory/memory_updater.py
git commit -m "feat(memory): implement generate_overview method"
```

---

### Task 4: 为events.yaml添加overview_template配置

**Files:**
- Modify: `openviking/prompts/templates/memory/events.yaml`

- [ ] **Step 1: 添加overview_template配置**

在文件末尾（fields之后）添加：

```yaml
overview_template: |
  # Events Overview

  {% for item in items %}
  - [{{ item.file_content.event_name }}]({{ item.file_name }}) - {{ item.file_content.summary }}
  {% endfor %}
```

- [ ] **Step 2: Commit**

```bash
git add openviking/prompts/templates/memory/events.yaml
git commit -m "feat(memory): add overview_template to events.yaml"
```

---

### Task 5: 移除edit_overview_uris相关代码

**Files:**
- Modify: `openviking/session/memory/dataclass.py`
- Modify: `openviking/session/memory/extract_loop.py`
- Modify: `openviking/session/memory/schema_model_generator.py`
- Modify: `openviking/session/memory/utils/uri.py`

- [ ] **Step 1: 移除dataclass.py中的edit_overview_uris字段**

从 `FlatMemoryOperations` 类中移除 `edit_overview_uris`（第208行）
从 `MemoryOperations` 类中移除（第234-237行）：
```python
    edit_overview_uris: List[Any] = Field(
        default_factory=list,
        description="Edit operations for .overview.md files using memory_type",
    )
```

修改 `is_empty` 方法，移除对 `edit_overview_uris` 的检查（第248行）
修改 `to_legacy_operations` 方法，移除对 `edit_overview_uris` 的返回（第257行）

- [ ] **Step 2: 移除extract_loop.py中的edit_overview_uris相关代码**

移除第122-123行：
```python
        # self._expected_fields = ["reasoning", "edit_overview_uris", "delete_uris"]
        self._expected_fields = ["delete_uris"]
```

改为：
```python
        self._expected_fields = ["delete_uris"]
```

- [ ] **Step 2: 移除schema_model_generator.py中的overview_edit_model相关代码**

- 移除第41-42行的类变量：
```python
    _generic_overview_edit_model: Optional[Type[BaseModel]] = None
```

- 移除第48行的初始化：
```python
        self._overview_edit_models: Dict[str, Type[BaseModel]] = {}
```

- 移除 `create_overview_edit_model` 方法（第124-160行）

- 移除第269-275行的注释代码：
```python
        # Use single generic model for overview edit (same for all memory types)
        # generic_overview_edit = self.create_overview_edit_model(
        #     MemoryTypeSchema(memory_type="overview", description="", fields=[])
        # )
        # field_definitions["edit_overview_uris"] = (
        #     List[generic_overview_edit],  # type: ignore
        #     Field(
        #         default=[],
        #         description="Edit operations for .overview.md files using memory_type",
        #     ),
        # )
```

- [ ] **Step 3: 移除utils/uri.py中的resolve_overview_edit_uri相关代码**

- 移除 `resolve_overview_edit_uri` 函数（第382-424行）
- 移除 `ResolvedOperations.edit_overview_operations` 相关代码（第433-437行、第497-502行、第560-562行）

- [ ] **Step 4: 运行测试验证**

Run: `pytest tests/session/memory/ -v --tb=short 2>&1 | head -50`
Expected: PASS (如有问题需要修复)

- [ ] **Step 5: Commit**

```bash
git add openviking/session/memory/extract_loop.py openviking/session/memory/schema_model_generator.py openviking/session/memory/utils/uri.py
git commit -m "refactor(memory): remove edit_overview_uris mechanism"
```

---

### Task 6: 集成测试

**Files:**
- Test: 运行现有memory相关测试

- [ ] **Step 1: 运行完整的memory测试套件**

Run: `pytest tests/session/memory/ -v --tb=short 2>&1 | tail -30`
Expected: ALL PASS

- [ ] **Step 2: 如果测试失败，修复问题并重新运行**

- [ ] **Step 3: Commit any fixes**

---

## 实现顺序

1. Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6
2. 建议使用 subagent-driven-development 模式，每个task由独立subagent执行

## 注意事项

- 在移除edit_overview_uris之前，确保新机制已经可以正常工作
- 模板中的field名称需要与实际frontmatter字段匹配
- 测试时注意events目录结构是否符合预期