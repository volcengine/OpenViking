---
name: build-one-page-index
description: Create or refresh a single One-Page knowledge index from a collection of documents that share one broad domain but cover substantially different subtopics. Use for knowledge portals, topic homepages, onboarding maps, document directories, and other cases where readers need one concise page to understand the landscape and navigate to the right material.
---

# One-Page 知识索引

将输入材料整理成一个可快速浏览、持续维护的主题导航页。读者应能在几分钟内理解大方向，并找到最适合继续阅读的材料。

## 整理方法

1. 识别所有材料共同服务的主题和主要读者。
2. 按读者的问题或使用场景归类，而不是机械复制文件夹结构。通常使用 4–8 个互不重叠的栏目。
3. 为每份有实质内容的材料选择一个主要栏目；合并重复材料，但不要遗漏独立子方向。
4. 提炼每份材料能解决的问题、适用对象或关键结论，不复制长段原文。

## 输出形态

只生成或更新一个普通 Wiki 页面，不要为每份材料分别生成子页。

页面建议包含：

- 标题：`<主题> One-Page`；
- 导语：2–4 句话说明覆盖范围、目标读者和使用方法；
- `内容索引`：作为正文主体，下设按主题归类的栏目；
- 每个栏目：一句范围说明，加若干条目；
- 每个条目：可读的材料名称，加一句用途或核心内容说明。

参考形态：

```markdown
# <主题> One-Page

<简短导语>

## 内容索引

### 1. <读者问题或子方向>

<本栏目的一句说明>

- <材料名称> — <读完能获得什么>
- <材料名称> — <适用场景或关键结论>
```

## 链接与可读性

- 材料有可用链接时，将材料名称作为可点击链接；没有链接时保留纯文本名称。
- 使用读者能理解的名称，不暴露内部编号、临时标识或实现细节。
- 保持导航页紧凑；详细步骤、数据表和长篇背景应留在原材料中。
- 栏目名称应平行、易扫读，避免连续使用“其他”或“杂项”。

## 事实要求

- 只使用材料中能够支持的事实，不猜测数字、状态、负责人或结论。
- 材料之间存在冲突时，使用中性语言指出，不自行选择其中一个。
- 如果输入材料并不共享一个可辨识的大方向，仍只产出一页，并在导语中说明分类边界。
