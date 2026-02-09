# Context Layers (L0/L1/L2)

OpenViking uses a three-layer information model to balance retrieval efficiency and content completeness.

## Overview

| Layer | Name | File | Token Limit | Purpose |
|-------|------|------|-------------|---------|
| **L0** | Abstract | `.abstract.md` | ~100 tokens | Vector search, quick filtering |
| **L1** | Overview | `.overview.md` | ~2k tokens | Rerank, content navigation |
| **L2** | Detail | Original files/subdirs | Unlimited | Full content, on-demand loading |

## L0: Abstract

The most concise representation of content, used for vector retrieval and quick filtering.

### Characteristics

- **Ultra-short**: Max ~100 tokens
- **Quick perception**: Allows Agent to quickly perceive content

### Example

```markdown
API authentication guide covering OAuth 2.0, JWT tokens, and API keys for secure access.
```

### API

```python
abstract = client.abstract("viking://resources/docs/auth")
```

## L1: Overview

Comprehensive summary with navigation guidance, used for Rerank and understanding access methods.

### Characteristics

- **Moderate length**: ~1k tokens
- **Navigation guide**: Tells Agent how to access detailed content

### Example

```markdown
# Authentication Guide Overview

This guide covers three authentication methods for the API:

## Sections
- **OAuth 2.0** (L2: oauth.md): Complete OAuth flow with code examples
- **JWT Tokens** (L2: jwt.md): Token generation and validation
- **API Keys** (L2: api-keys.md): Simple key-based authentication

## Key Points
- OAuth 2.0 recommended for user-facing applications
- JWT for service-to-service communication

## Access
Use `read("viking://resources/docs/auth/oauth.md")` for full documentation.
```

### API

```python
overview = client.overview("viking://resources/docs/auth")
```

## L2: Detail

Complete original content, loaded only when needed.

### Characteristics

- **Full content**: No token limit
- **On-demand loading**: Read only when confirmed necessary
- **Original format**: Preserves source structure

### API

```python
content = client.read("viking://resources/docs/auth/oauth.md")
```

## Generation Mechanism

### When Generated

- **When adding resources**: After Parser parsing, SemanticQueue generates asynchronously
- **When archiving sessions**: L0/L1 generated for history segments during compression

### Who Generates

| Component | Responsibility |
|-----------|----------------|
| **SemanticProcessor** | Traverses directories bottom-up, generates L0/L1 for each |
| **SessionCompressor** | Generates L0/L1 for archived session history |

### Generation Order

```
Leaf nodes → Parent directories → Root (bottom-up)
```

Child directory L0s are aggregated into parent L1, forming hierarchical navigation.

## Directory Structure

Each directory follows a unified file structure:

```
viking://resources/docs/auth/
├── .abstract.md          # L0: ~100 tokens
├── .overview.md          # L1: ~1k tokens
├── .relations.json       # Related resources
├── oauth.md              # L2: Full content
├── jwt.md                # L2: Full content
└── api-keys.md           # L2: Full content
```

## Multimodal Support

- **L0/L1**: Always text (Markdown)
- **L2**: Can be any format (text, image, video, audio)

For binary content, L0/L1 describe in text:

```markdown
# Image L0
Product screenshot showing login page with OAuth buttons.

# Image L1
## Image: Login Page Screenshot

This screenshot shows the application's login page with:
- Google OAuth button (top)
- GitHub OAuth button (middle)
- Email/password form (bottom)

Dimensions: 1920x1080, Format: PNG
```

Directory Structure

```
...
└── Chapter 3 Developer Notes/
    ├── .abstract.md
    ├── .overview.md
    ├── content.md
    └── Video Attachment 1 - Developer Notes/              ← Recursive expansion of attachment info
        ├── .abstract.md
        ├── .overview.md
        ├── audio_and_subtitles.md
        ├── developer_training.mp4
        └── video_segments/
            ├── developer_training_0s-30s.mp4
            └── developer_training_30s-60s.mp4
```



## Best Practices

| Scenario | Recommended Layer |
|----------|-------------------|
| Quick relevance check | L0 |
| Understand content scope | L1 |
| Detailed information extraction | L2 |
| Building context for LLM | L1 (usually sufficient) |

### Token Budget Management

```python
# Use L1 first, load L2 only when needed
overview = client.overview(uri)

if needs_more_detail(overview):
    content = client.read(uri)
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Context Types](./02-context-types.md) - Three context types
- [Viking URI](./04-viking-uri.md) - URI specification
- [Retrieval Mechanism](./07-retrieval.md) - Retrieval process details
- [Context Extraction](./06-extraction.md) - L0/L1 generation details
