# OpenViking Prompt Guide and Customization

This document introduces OpenViking's current prompt template system, with a focus on:

- what prompts currently exist
- which processing stage each prompt is used in
- which external capabilities or results each prompt affects
- the format requirements for template files
- how to customize prompts safely

This document only covers templates under `openviking/prompts/templates/` plus a small set of configuration items related to template loading.

## Overview

OpenViking's current prompts fall into two main groups:

1. Regular prompt templates
   - Stored under `openviking/prompts/templates/<category>/*.yaml`
   - Used to instruct the model to perform tasks such as image understanding, document summarization, memory extraction, and retrieval intent analysis
2. Memory schema templates
   - Stored under `openviking/prompts/templates/memory/*.yaml`
   - Used to define the fields, filename templates, content templates, and directory rules for a memory type

From a usage perspective, these templates mainly serve the following processing stages:

| Category | Representative template | Main purpose | Effective stage | External capability affected |
|----------|-------------------------|--------------|-----------------|------------------------------|
| `vision` | `vision.image_understanding` | Image, page, and table understanding | Resource parsing and scanned-document understanding | Image parsing, PDF page understanding, table extraction results |
| `parsing` | `parsing.context_generation` | Document structure splitting and semantic node generation | Resource ingestion and parsing | Document chapter structure, node summaries, image summaries |
| `semantic` | `semantic.document_summary` | File-level and directory-level summaries | Semantic indexing | File summaries, directory overviews, downstream retrieval quality |
| `retrieval` | `retrieval.intent_analysis` | Retrieval intent analysis and query planning | Pre-retrieval analysis | Search query planning and context recall direction |
| `compression` | `compression.memory_extraction` | Memory extraction, merging, compression, and summarization | Session commit / memory pipeline | Long-term memory extraction, session compression, memory merge results |
| `memory` | `profile` | Memory type definitions | Memory persistence and updates | The organization and final content of different memory types |
| `processing` | `processing.tool_chain_analysis` | Extracting experience from interactions or resource background | Post-processing and experience distillation | Strategy extraction, tool-chain experience, interaction learning results |
| `indexing` | `indexing.relevance_scoring` | Candidate relevance evaluation | Retrieval and indexing support | Relevance scoring quality |
| `skill` | `skill.overview_generation` | Skill information distillation | Skill resource processing | Skill retrieval summaries |
| `test` | `test.skill_test_generation` | Automatic test case generation | Test and validation support | Skill test case generation |

## Prompt Format Requirements

### Regular Prompt YAML

A regular prompt template usually contains the following fields:

```yaml
metadata:
  id: "semantic.document_summary"
  name: "Document Summary"
  description: "Generate summary for documentation files"
  version: "1.0.0"
  language: "en"
  category: "semantic"

variables:
  - name: "file_name"
    type: "string"
    description: "Input file name"
    required: true

template: |
  ...

output_schema:
  ...

llm_config:
  ...
```

Field meanings:

- `metadata`
  - Describes the template identity and category
  - `id` usually corresponds to the file path, for example `semantic.document_summary`
- `variables`
  - Defines the input variables accepted by the template
  - Common fields include `name`, `type`, `description`, `default`, `required`, and `max_length`
- `template`
  - The actual prompt body sent to the model
  - Rendered with Jinja2 variables
- `output_schema`
  - Optional
  - Describes the expected output structure so callers can constrain model output
- `llm_config`
  - Optional
  - Describes suggested model-side parameters and is not part of the prompt body itself

When writing a regular prompt, follow these guidelines:

- Keep `metadata.id` consistent with the template category and purpose
- Keep variable names stable so they stay compatible with callers
- Ensure placeholders inside `template` match the definitions in `variables`
- If the template expects structured output, specify the fields, format, and constraints clearly
- If the input is length-sensitive, control prompt size through `max_length` or upstream truncation

### Memory Schema YAML

`memory/*.yaml` files are not regular prompt text templates. They define memory types. The example below is only a schematic structure showing common fields. Whether a built-in template includes `content_template`, or whether its directory uses a subdirectory, depends on the specific memory type.

```yaml
memory_type: "profile"
description: "User profile memory"
fields:
  - name: "content"
    type: "string"
    description: "Profile content"
    merge_op: "patch"
filename_template: "profile.md"
content_template: |
  ...
directory: "viking://user/{{ user_space }}/memories/..."
enabled: true
operation_mode: "upsert"
```

Field meanings:

- `memory_type`
  - The name of the memory type
- `description`
  - The definition of the memory type and its extraction requirements
- `fields`
  - The fields included in this memory type
- `filename_template`
  - The template used to generate the file name
- `content_template`
  - The body template used when writing the memory file
- `directory`
  - The directory where this memory type is stored
- `enabled`
  - Whether this memory type is enabled
- `operation_mode`
  - The update mode of the memory type, such as `upsert`

When writing a memory schema, focus on:

- whether the field granularity is stable
- whether the filename template is predictable and searchable
- whether the directory rule matches the intended retrieval scope
- whether the merge strategy is appropriate for that memory type

## Current Prompt Template Reference

The sections below list all current templates by category. Each entry explains which processing stage it belongs to and which external capabilities it mainly affects.

When reading this section, a simple rule helps:

- For regular prompt templates, focus on `Purpose` and `Key inputs`
- For memory schemas, focus on `Purpose` and `Key fields`

### Compression

These prompts are mainly used for session compression, memory extraction, memory merging, and field compression. They are a core part of long-term memory quality.

- `compression.dedup_decision`
  - Effective stage: memory candidate deduplication and decision stage
  - Affects: long-term memory deduplication and create/merge strategy
  - Purpose: decides whether a new memory candidate should be skipped, created, or merged into existing memory
  - Key inputs: `candidate_content`, `candidate_abstract`, `candidate_overview`, `existing_memories`

- `compression.field_compress`
  - Effective stage: memory field compression stage
  - Affects: controllable length and readability of long fields such as tool memories
  - Purpose: compresses field content while preserving key information
  - Key inputs: `field_name`, `content`, `max_length`

- `compression.memory_extraction`
  - Effective stage: post-compression memory extraction stage
  - Affects: long-term memory extraction quality and downstream recall hit rate
  - Purpose: extracts memory candidates worth preserving from session summary and recent messages
  - Key inputs: `summary`, `recent_messages`, `user`, `feedback`, `output_language`

- `compression.memory_merge`
  - Effective stage: single-memory merge stage
  - Affects: content quality after updating existing memory
  - Purpose: merges existing memory with new information into a more complete version
  - Key inputs: `existing_content`, `new_content`, `category`, `output_language`

- `compression.memory_merge_bundle`
  - Effective stage: structured memory merge stage
  - Affects: merged L0/L1/L2 memory output
  - Purpose: returns merged `abstract`, `overview`, and `content` in one call
  - Key inputs: `existing_abstract`, `existing_overview`, `existing_content`, `new_abstract`, `new_overview`, `new_content`, `category`, `output_language`

- `compression.structured_summary`
  - Effective stage: session archive summary generation stage
  - Affects: archived session summaries and downstream review/retrieval quality
  - Purpose: generates a structured summary for archived sessions
  - Key inputs: `latest_archive_overview`, `messages`

### Indexing

This category is mainly used to support retrieval or indexing workflows with relevance judgments.

- `indexing.relevance_scoring`
  - Effective stage: candidate relevance evaluation stage
  - Affects: retrieval ranking and candidate filtering quality
  - Purpose: evaluates how relevant candidate content is to the user's query
  - Key inputs: `query`, `candidate`

### Memory

These YAML files define the structure of different memory types. They are not single-inference prompts. Together, they determine how user memories and agent memories are stored, updated, and used by later retrieval.

- `cases`
  - Effective stage: case-memory persistence and update stage
  - Affects: reusable problem-to-solution case accumulation
  - Purpose: defines case memory for "what problem happened and how it was solved"
  - Key fields: `case_name`, `problem`, `solution`, `content`

- `entities`
  - Effective stage: entity-memory persistence and update stage
  - Affects: long-term storage of people, projects, organizations, systems, and other entities
  - Purpose: defines the storage structure for named entities and their attributes
  - Key fields: `category`, `name`, `content`

- `events`
  - Effective stage: event-memory persistence and update stage
  - Affects: event review, timeline-aware retention, and conversation narrative recording
  - Purpose: defines structured event memory such as summaries, goals, and time ranges
  - Key fields: `event_name`, `goal`, `summary`, `ranges`

- `identity`
  - Effective stage: agent identity memory persistence stage
  - Affects: long-term consistency of the agent's identity settings
  - Purpose: defines the agent's name, persona, vibe, avatar, and self-introduction fields
  - Key fields: `name`, `creature`, `vibe`, `emoji`, `avatar`

- `patterns`
  - Effective stage: pattern-memory persistence and update stage
  - Affects: long-term accumulation of reusable workflows and methods
  - Purpose: defines pattern memory for "under what circumstances to follow what process"
  - Key fields: `pattern_name`, `pattern_type`, `content`

- `preferences`
  - Effective stage: preference-memory persistence and update stage
  - Affects: user preference recall and downstream personalization behavior
  - Purpose: defines user preference memory under different topics
  - Key fields: `user`, `topic`, `content`

- `profile`
  - Effective stage: user profile memory persistence and update stage
  - Affects: long-term storage of user profile, work background, and stable attributes
  - Purpose: defines the storage structure for "who the user is"
  - Key fields: `content`

- `skills`
  - Effective stage: skill-usage memory persistence and update stage
  - Affects: skill usage statistics, experience accumulation, and recommended workflows
  - Purpose: defines skill usage counts, success rates, best-fit scenarios, and related information
  - Key fields: `skill_name`, `total_executions`, `success_count`, `fail_count`, `best_for`, `recommended_flow`

- `soul`
  - Effective stage: agent soul memory persistence stage
  - Affects: the agent's core boundaries, continuity, and long-term identity stability
  - Purpose: defines the agent's core truths, boundaries, vibe, and continuity
  - Key fields: `core_truths`, `boundaries`, `vibe`, `continuity`

- `tools`
  - Effective stage: tool-usage memory persistence and update stage
  - Affects: tool usage experience, optimal parameters, and failure pattern accumulation
  - Purpose: defines the storage structure for tool call statistics and tool-usage experience
  - Key fields: `tool_name`, `static_desc`, `call_count`, `success_time`, `when_to_use`, `optimal_params`

### Parsing

These prompts are mainly used to convert raw resource content into structured nodes, chapters, summaries, or image overviews that are easier to retrieve and understand.

- `parsing.chapter_analysis`
  - Effective stage: long-document chapter splitting stage
  - Affects: document chapter structure and page organization
  - Purpose: analyzes document content and splits it into a reasonable chapter structure
  - Key inputs: `start_page`, `end_page`, `total_pages`, `content`

- `parsing.context_generation`
  - Effective stage: document node semantic generation stage
  - Affects: node abstract/overview quality and downstream retrieval matching
  - Purpose: generates shorter, retrieval-friendly semantic titles, abstracts, and overviews for text nodes
  - Key inputs: `title`, `content`, `children_info`, `instruction`, `context_type`, `is_leaf`

- `parsing.image_summary`
  - Effective stage: image node summarization stage
  - Affects: semantic overviews of image resources and downstream retrieval
  - Purpose: generates a concise summary for image content
  - Key inputs: `context`

- `parsing.semantic_grouping`
  - Effective stage: semantic grouping and splitting stage
  - Affects: document node granularity and content chunking quality
  - Purpose: decides whether content should be merged or split based on semantics
  - Key inputs: `items`, `threshold`, `mode`

### Processing

These prompts are mainly used to distill strategies or experience from interaction records, tool chains, and resource background. They are used for post-processing and knowledge accumulation rather than direct one-turn user answering.

- `processing.interaction_learning`
  - Effective stage: post-interaction experience extraction stage
  - Affects: reusable interaction experience and distillation of effective resources and successful skills
  - Purpose: extracts reusable experience from interaction records
  - Key inputs: `interactions_summary`, `effective_resources`, `successful_skills`

- `processing.strategy_extraction`
  - Effective stage: post-resource-addition strategy extraction stage
  - Affects: structured extraction and reuse of resource background intent
  - Purpose: extracts usage strategies from the reason, instruction, and abstract associated with resource addition
  - Key inputs: `reason`, `instruction`, `abstract`

- `processing.tool_chain_analysis`
  - Effective stage: tool-chain analysis stage
  - Affects: tool combination pattern recognition and tool experience accumulation
  - Purpose: analyzes tool call chains and identifies valuable usage patterns
  - Key inputs: `tool_calls`

### Retrieval

These prompts are mainly used to understand user intent before retrieval and decide the query plan and context type.

- `retrieval.intent_analysis`
  - Effective stage: pre-retrieval intent analysis stage
  - Affects: retrieval query planning, recall direction, and search quality across different context types
  - Purpose: generates a retrieval plan using compressed summary, recent messages, and the current message
  - Key inputs: `compression_summary`, `recent_messages`, `current_message`, `context_type`, `target_abstract`

### Semantic

These prompts are mainly used to generate file-level and directory-level summaries and are an important part of semantic indexing.

- `semantic.code_ast_summary`
  - Effective stage: AST skeleton summarization for large code files
  - Affects: code file summaries, code retrieval, and structural understanding
  - Purpose: generates code summaries from an AST skeleton instead of the full source
  - Key inputs: `file_name`, `skeleton`, `output_language`

- `semantic.code_summary`
  - Effective stage: code file summarization stage
  - Affects: semantic indexing for code files, code retrieval, and understanding results
  - Purpose: generates summaries for code files with a focus on structure, functions, classes, and key logic
  - Key inputs: `file_name`, `content`, `output_language`

- `semantic.document_summary`
  - Effective stage: documentation file summarization stage
  - Affects: document summaries, document retrieval, and overview quality
  - Purpose: generates summaries for Markdown, text, RST, and similar documentation files
  - Key inputs: `file_name`, `content`, `output_language`

- `semantic.file_summary`
  - Effective stage: generic file summarization stage
  - Affects: directory indexing and generic file retrieval quality
  - Purpose: generates a summary for a single file as upstream input for directory abstract/overview generation
  - Key inputs: `file_name`, `content`, `output_language`

- `semantic.overview_generation`
  - Effective stage: directory overview generation stage
  - Affects: directory overviews, hierarchical retrieval, and navigation experience
  - Purpose: generates a directory-level overview from file summaries and child directory abstracts
  - Key inputs: `dir_name`, `file_summaries`, `children_abstracts`, `output_language`

### Skill

These prompts are mainly used to compress Skill content into summaries suitable for retrieval and reuse.

- `skill.overview_generation`
  - Effective stage: Skill content processing stage
  - Affects: Skill retrieval summaries and Skill discovery quality
  - Purpose: extracts key retrieval information from a Skill's name, description, and content
  - Key inputs: `skill_name`, `skill_description`, `skill_content`

### Test

These prompts are mainly used to help generate test cases.

- `test.skill_test_generation`
  - Effective stage: Skill testing support stage
  - Affects: Skill scenario test design and validation sample generation
  - Purpose: generates test cases from the names and descriptions of multiple Skills
  - Key inputs: `skills_info`

### Vision

These prompts are mainly used for image, page, table, and multimodal document analysis, and directly affect image parsing and scanned-document understanding.

- `vision.batch_filtering`
  - Effective stage: multi-image batch filtering stage
  - Affects: keep/drop decisions for images in multi-image document understanding
  - Purpose: determines in batch whether multiple images are worth including in document understanding
  - Key inputs: `document_title`, `image_count`, `images_info`

- `vision.image_filtering`
  - Effective stage: single-image filtering stage
  - Affects: whether an image enters downstream understanding workflows
  - Purpose: determines whether a single image is meaningful for document understanding
  - Key inputs: `document_title`, `context`

- `vision.image_understanding`
  - Effective stage: image understanding stage
  - Affects: image parsing results and the quality of image `abstract`, `overview`, and `detail_text`
  - Purpose: uses the VLM to generate three-layer information for an image
  - Key inputs: `instruction`, `context`

- `vision.page_understanding`
  - Effective stage: scanned-page understanding stage
  - Affects: scanned PDF page understanding and downstream semantic results
  - Purpose: understands a single image-based document page
  - Key inputs: `instruction`, `page_num`

- `vision.page_understanding_batch`
  - Effective stage: multi-page batch understanding stage
  - Affects: efficiency and consistency when understanding scanned pages in batch
  - Purpose: batch-understands multiple image-based document pages
  - Key inputs: `page_count`, `instruction`

- `vision.table_understanding`
  - Effective stage: table understanding stage
  - Affects: table-image parsing, table summaries, and structural understanding
  - Purpose: analyzes a table image and generates three-layer information
  - Key inputs: `instruction`, `context`

- `vision.unified_analysis`
  - Effective stage: unified multimodal analysis stage
  - Affects: parsing results for complex documents that include images, tables, and chapters
  - Purpose: batch-analyzes document images, tables, and chapter-related information
  - Key inputs: `title`, `instruction`, `reason`, `content_preview`, `image_count`, `images_section`, `table_count`, `tables_section`

## How to Customize Prompts

OpenViking supports two main customization patterns:

1. Override regular prompt templates
2. Extend memory schemas

Before going into the specific methods, you can use the following table to judge change risk:

| Change type | Risk level | Notes |
|-------------|------------|-------|
| Changing prompt wording, adding examples, adjusting tone | Low | Usually only changes model behavior style and does not change the caller contract |
| Changing output style, extraction preference, or summary granularity | Medium | Changes result distribution and should be revalidated against the target capability |
| Changing variable names, output structure, or memory field names | High | Easy to break compatibility with callers or parsing logic |
| Changing `directory`, `filename_template`, or `merge_op` | Very high | Directly changes memory storage location, organization, and update behavior |

### Override Regular Prompt Templates

Applicable when:

- you want to adjust memory extraction preferences
- you want to change summarization style
- you want image understanding output to be more detailed or more concise
- you want to change retrieval intent planning behavior

Available configuration:

- `prompts.templates_dir`
- environment variable `OPENVIKING_PROMPT_TEMPLATES_DIR`

Load priority:

1. Explicitly provided template directory
2. Environment variable `OPENVIKING_PROMPT_TEMPLATES_DIR`
3. `prompts.templates_dir` in `ov.conf`
4. Built-in template directory `openviking/prompts/templates/`

In other words, regular prompt customization works by checking the custom directory first and falling back to the built-in template when the same relative path is not found.

Recommended approach:

1. Copy the target file from the built-in template directory first
2. Keep the same category directory and file name
3. Only modify the prompt body or output requirements
4. Avoid changing variable names that callers already depend on

Example directory:

```text
custom-prompts/
├── compression/
│   └── memory_extraction.yaml
├── retrieval/
│   └── intent_analysis.yaml
└── semantic/
    └── document_summary.yaml
```

Example configuration:

```json
{
  "prompts": {
    "templates_dir": "/path/to/custom-prompts"
  }
}
```

Or:

```bash
export OPENVIKING_PROMPT_TEMPLATES_DIR=/path/to/custom-prompts
```

Impact examples:

- Modifying `compression.memory_extraction`
  - mainly affects the memory extraction stage
  - ultimately affects long-term memory quality and downstream recall results
- Modifying `retrieval.intent_analysis`
  - mainly affects pre-retrieval query planning
  - ultimately affects search direction and recall quality
- Modifying `semantic.document_summary`
  - mainly affects document summarization
  - ultimately affects document indexing and summary output

### Extend Memory Schemas

Applicable when:

- you want to add a new business-specific memory type
- you want to adjust the field structure of an existing memory type
- you want to change memory storage directories or filename templates

Available configuration:

- `memory.custom_templates_dir`

Loading behavior:

- Built-in memory schemas are loaded first
- If `memory.custom_templates_dir` is configured, schemas in that directory are loaded afterward
- As a result, memory customization behaves more like extension and supplementation than a full replacement of the built-in set

Example directory:

```text
custom-memory/
├── project_decisions.yaml
└── user_preferences_ext.yaml
```

Example configuration:

```json
{
  "memory": {
    "custom_templates_dir": "/path/to/custom-memory"
  }
}
```

Recommendations when extending memory schemas:

- Start by following the style of existing `memory/*.yaml` files
- Confirm that the new memory type really needs to be independent
- Keep field names clear and stable for future updates
- Ensure `directory` and `filename_template` are easy to search and maintain

Impact examples:

- Adding `project_decisions`
  - affects memory persistence types and downstream search organization
- Modifying `preferences`
  - affects how user preference memories are organized and the granularity of recall
- Modifying `tools`
  - affects tool experience accumulation and tool usage recommendations

### High-Risk Changes During Customization

The following changes are the most likely to break existing workflows:

- changing variable names in regular prompts
- changing the expected output structure of a prompt without updating downstream parsing logic
- changing key field names in a memory schema
- changing `directory`, which changes retrieval scope
- changing `filename_template`, which changes how historical files are organized
- changing `merge_op`, which changes how existing memories are updated

If your goal is only to improve quality, these are usually the safer first moves:

- add clearer output examples to the prompt
- strengthen rules about what to keep and what to ignore
- adjust summary granularity or response style
- modify only one prompt category at a time instead of several at once

A conservative approach is:

1. copy the existing template first
2. change the instruction content and phrasing first
3. change structural fields last
4. modify only one prompt category at a time so impact is easier to isolate

## Validation and Troubleshooting

After modifying a prompt, validate it at two levels: whether the template was actually picked up, and whether the capability output changed as expected.

### First: Verify the Template Was Picked Up

Checklist:

- whether the custom directory is configured correctly
- whether the file path keeps the same relative path as the original template
- whether the YAML is valid
- whether the variable names still match the original template

For a regular prompt, focus on:

- whether the template was loaded correctly
- whether the target stage really uses that template

For a memory schema, focus on:

- whether the new schema was loaded successfully
- whether the target memory type actually participates in extraction and persistence

### Then: Verify That External Results Changed

The most effective validation is capability-focused:

- If you changed a `vision` template, re-parse images, tables, or scanned PDFs and check whether the results changed
- If you changed a `semantic` or `parsing` template, re-import documents or files and check whether summaries and structure changed
- If you changed a `retrieval` template, rerun the relevant search and check whether query planning and recall behavior changed
- If you changed a `compression` template, re-trigger session commit or memory processing and check whether extraction and merge results changed
- If you changed a `memory` schema, inspect the final persisted memory files, directories, and field structure

### Common Troubleshooting Patterns

Symptoms and first things to check:

| Symptom | First thing to check |
|---------|----------------------|
| Results do not change at all after modification | The custom directory is not active, or the file path does not match |
| The model reports missing variables | Template variable names do not match what callers provide |
| Returned content format is broken | The prompt output format changed, but downstream parsing still expects the old structure |
| A new memory type never appears | `memory.custom_templates_dir` is not active, or the schema was not loaded correctly |
| Retrieval quality gets worse | The `retrieval`, `semantic`, or `compression` prompt was changed too aggressively |

## Appendix

### Template Directory

Built-in prompt template directory:

```text
openviking/prompts/templates/
```

It contains:

- `compression/`: compression, extraction, and merging
- `indexing/`: relevance evaluation
- `memory/`: memory type definitions
- `parsing/`: structure analysis and semantic node generation
- `processing/`: experience and strategy extraction
- `retrieval/`: retrieval intent analysis
- `semantic/`: file and directory summaries
- `skill/`: Skill summaries
- `test/`: test case generation
- `vision/`: image, page, and table understanding

### Key Configuration Items

The main configuration items related to prompt customization are:

| Configuration item | Purpose |
|--------------------|---------|
| `prompts.templates_dir` | Override directory for regular prompt templates |
| `OPENVIKING_PROMPT_TEMPLATES_DIR` | Environment variable for the override directory of regular prompt templates |
| `memory.custom_templates_dir` | Directory for custom memory schemas |

### Practical Rule of Thumb

If your goal is:

- to change how the model speaks, extracts, or summarizes
  - modify a regular prompt template first
- to change what a memory looks like, where it is stored, or how it is organized
  - modify or extend a memory schema first

If you are not sure which layer to modify, ask yourself:

"Am I changing the model's instruction, or the structure of the final memory file?"

That question is usually enough to help decide whether you should modify a regular prompt or a memory schema.
