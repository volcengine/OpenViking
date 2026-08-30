# Published Wiki Read

Use this reference only when the user explicitly asks for a published, team,
cloud, or other-contributor Wiki. Prefer the local operation in `repo-read.md`
for the current checkout.

## Resolve Scope

Use the narrowest requested subtree:

```text
current user's Wiki: viking://resources/wiki/<repo-id>/<current-user-id>
selected user's Wiki: viking://resources/wiki/<repo-id>/<selected-user-id>
all contributors:      viking://resources/wiki/<repo-id>
```

Do not search the whole Resource namespace when the repository Wiki root is
known. Resolve the stable repo id from the same normalized repository identity
used by the upload plugin.

## Retrieval

- Use `grep` for exact symbols, errors, configuration keys, or quoted wording.
- Use `glob` for page names and paths.
- Use `find` for architecture, routing, history, and design-rationale concepts.
- Use `read` only after selecting a small set of pages.
- Use `list` or `tree` only when the Wiki structure is unknown.

## Server-Side Wiki Discovery And File Access

Cloud Wiki access mirrors local Wiki access, but uses OpenViking MCP filesystem
operations over `viking://` URIs rather than shell commands against a local
directory. Do not pass a `viking://` URI to Bash, `rg`, `cat`, or other local
filesystem commands.

### List the Wiki owners for the current repository

When the user asks which published Wikis exist for a repository, first list the
repository root:

```text
list(uri="viking://resources/wiki/<repo-id>", recursive=false)
```

Each child directory is a Wiki owner. If the root is empty, report that no Wiki
has been published for this repository under the current service identity. Do
not infer an owner from local filesystem paths.

### Select one Wiki, then inspect it like a local `.repo_memory` bundle

After choosing an owner directory, use its exact URI:

```text
viking://resources/wiki/<repo-id>/<owner>
```

Use this mapping:

| Local Wiki operation | Server-side MCP operation |
|---|---|
| `ls .repo_memory` | `list(uri=<wiki-uri>, recursive=false)` |
| `find .repo_memory -name '*.md'` | `glob(uri=<wiki-uri>, pattern="**/*.md")` |
| `rg -n 'pattern' .repo_memory` | `grep(uri=<wiki-uri>, pattern=["pattern"])` |
| `cat .repo_memory/PAGE.md` | `read(uris=[<wiki-uri>/PAGE.md])` |
| Semantic question over local pages | `find(query=..., target_uri=<wiki-uri>)` |

`grep` accepts regular-expression patterns and returns matching file URIs, line
numbers, and content snippets. It is the server-side equivalent for exact
`rg`/grep-style content lookup, but it does not execute the Unix `rg` binary
or accept arbitrary shell flags. Combine `glob` for path filtering with `grep`
for content filtering.

### Minimal discovery sequence

1. `list` the repository Wiki root to discover owners.
2. Select the requested owner, or the current user when the request is scoped
   to their Wiki.
3. `glob` Markdown pages if the page set is unknown.
4. Use `grep` for exact symbols/phrases or `find` for conceptual questions.
5. `read` only the selected page files.

Keep the selected owner URI fixed through the retrieval operation. Do not merge
different owners' pages into one conclusion without attribution.

Identify the backend, repository, Wiki owner, URI, source commit, generated
time, and freshness when available. Group results by owner; do not merge
conflicting contributor claims into an unattributed team conclusion.

Published Wiki is an ordinary Resource in the plugin-only MVP, so generic
semantic recall can also return it outside this explicit operation. Verify
current implementation claims against the live checkout and focused tests.
