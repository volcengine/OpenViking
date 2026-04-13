# Memory Overview 自动生成机制设计

## 背景

当前extract_loop中，overview文件的更新由LLM通过`edit_overview_uris`决定，存在以下问题：
1. LLM需要额外推理来决定如何更新overview
2. 更新逻辑不统一，依赖prompt质量
3. 每次都要读取所有已有overview内容，效率较低

## 目标

在每个memory类型的yaml配置中新增`overview_template`字段，基于模板自动生成overview文件，替换原有的LLM决定机制。

## 设计方案

### 1. YAML配置扩展

在每个memory类型的yaml文件中新增`overview_template`字段：

```yaml
# events.yaml 示例
memory_type: events
description: |
  ...
overview_template: |
  # {{ memory_type|capitalize }} Overview

  {% for item in items %}
  - [{{ item.event_name }}]({{ item.filename }}) - {{ item.summary }}
  {% endfor %}
```

配置解析时，将此字段存入`MemoryTypeSchema`结构。

### 2. 数据结构

**传递到模板的items包含：**
- frontmatter所有字段（如event_name, summary, time等）
- filename（包含后缀，如`event1.md`）

uri在模板中根据需求重新渲染生成，不包含在meta dict中。

### 3. 渲染时机

在`memory_updater.py`中，当处理完增删改操作后：

1. 检查该memory类型是否配置了`overview_template`
2. 如果配置了：
   - 找到该目录下所有.md文件（排除.overview.md和.abstract.md）
   - 使用现有`parse_memory_file_with_fields`解析每个文件为meta dict
   - 收集所有items
   - 使用Jinja2渲染模板
   - 写入`.overview.md`文件

### 4. Overview文件位置

**文件同级目录**：每个有memory文件的目录生成独立的`.overview.md`

例如：
- `events/2024/01/01/event1.md` → `events/2024/01/01/.overview.md`
- `events/2024/01/02/event2.md` → `events/2024/01/02/.overview.md`

只有当目录内有memory文件时才生成overview。

### 5. 实现位置

- **YAML解析**：`memory_type_registry.py`的`_parse_memory_type`方法
- **生成逻辑**：`memory_updater.py`中新增`generate_overview`方法
- **调用入口**：在`apply_operations`方法中，增删改操作完成后调用

### 6. 删除原有机制

移除以下代码：
- `dataclass.py`中的`edit_overview_uris`字段
- `schema_model_generator.py`中的`_generic_overview_edit_model`和`create_overview_edit_model`
- `extract_loop.py`中的`edit_overview_uris`相关逻辑
- `utils/uri.py`中的`resolve_overview_edit_uri`相关函数

## 迁移方案

1. 先实现新机制（基于模板生成overview）
2. 移除`edit_overview_uris`相关代码
3. 测试验证功能正常

## 风险与错误处理

- 如果目录下没有memory文件，不生成overview
- 如果模板渲染失败，记录日志并跳过该目录
- 如果`overview_template`未配置，则不执行自动生成（该memory类型保持原有行为）

## 测试计划

1. 单元测试：验证模板渲染逻辑
2. 集成测试：验证增删改后overview正确生成
3. 手动测试：检查各memory类型的overview输出格式