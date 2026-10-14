# Data organization guide

This guide defines how to organize Resource data in OpenViking. Directories establish visibility, retrieval scope, and collaboration boundaries.

## Context types

| Type | Content | Write and organization model |
| --- | --- | --- |
| Resource | Documents, sites, repositories, datasets, and compiled knowledge bases | Write with `add_resource`, `ov add-resource`, or `ov compile`; organize with this guide |
| Memory | Preferences, facts, events, and experience extracted from interactions | Write through session commit and other memory-extraction flows; let the memory schema and extraction system organize it |
| Skill | Repeatable methods, constraints, scripts, and supporting material | Install with `add_skill` or `ov add-skill` |

The directory conventions in this guide apply only to Resource data. Do not mirror them under `memories/`.

## Visibility

| Location | Visibility | Use |
| --- | --- | --- |
| `viking://resources/` | Shared within the current account | Material used by multiple identities; ACLs provide finer access control |
| `viking://~/resources/` | Private to the current authenticated identity | Material used independently by the current identity |
| `viking://~/peers/<peer>/resources/` | One peer under the current identity | Material associated with a stable interaction peer |

An OpenViking `user` is an authenticated context owner and may represent a person, agent, application, or service identity. `viking://~` always refers to the current request identity's own user space.

The `account_id` already forms the team-sharing boundary, so another `teams/<team>/` layer is unnecessary. Do not upload passwords, tokens, private keys, or other secrets as ordinary Resources.

## Directory conventions

`projects/<project>/` is an optional collaboration container. Use it only when the project name is stable and multiple identities need to share, retrieve, and maintain the same context. A project directory does not replace account or ACL visibility controls.

The other category directories may sit directly below a Resource root or inside one project directory. Create them only as needed; a space does not need the full set. These names are conventions that prevent different agents from creating synonymous directories such as `doc`, `docs`, `documents`, and even `pdfs`. The table defines the common names; their full URIs follow the same pattern as `viking://resources/repos`.

| Directory | Content | Boundary |
| --- | --- | --- |
| `repos/` | Complete code repositories | Preserve the repository structure; do not split by programming language |
| `docs/` | Manuals, specifications, product documentation, Office files, papers, preprints, and other document snapshots | Put reproduction code and independent datasets in `repos/` and `datasets/` |
| `sites/` | Content sourced from a URL, domain, or feed and imported or refreshed as a web tree | Put a discrete offline document in `docs/` |
| `wiki/` | Knowledge bases that have been organized, deduplicated, restructured, and linked or indexed | Use as a stable output for LLM Wiki and similar `ov compile` tasks; keep raw sources in their source directories |
| `datasets/` | Tables, corpora, evaluation sets, structured archives, observations, and training or analysis data | Point clouds and GIS observations are datasets |
| `media/` | Finished images, audio, and video intended for direct viewing or listening | Put editable sources and engineering outputs in `artifacts/` |
| `artifacts/` | Non-document outputs used by editing, manufacturing, rendering, or runtime workflows | Includes drawings, CAD, 3D models, scenes, visual source files, templates, and brand material |
| `items/` | Entity collection organized by commodity, product, SKU, or another business object | Put bulk tables and training data in `datasets/` |
| `people/` | Entity collection organized by person, customer, contact, or another identity | User space, account boundaries, and ACLs still control visibility and sensitive information |
| `inbox/` | Resources not yet classified by the current identity | Recommended only under `viking://~/resources/`; not a durable account-shared category |
| `projects/<project>/` | Project collaboration context | Optional container; when a project genuinely needs one, continue using the categories in this table below it |

`items/` and `people/` are examples of entity collection directories, not reserved names. An agent may use `companies/`, `positions/`, or another clear plural noun for the domain. These directories are ordinary Resource directories today and align with a future Item retrieval capability; the current release provides no dedicated Item retrieval semantics.

For time-series material (daily reports, email archives, periodic reports), layer it by date inside the category rather than creating a category per date. Path variables can be used directly in the URI: `viking://resources/docs/{calendar:ym}/` renders to `docs/2026/05/`. Common variables include `{calendar:today}`, `{calendar:ym}`, `{calendar:yq}`, and `{calendar:yw}`; the CLI, SDK, and MCP pass the template as-is and the server resolves it. See [Viking URI path variables](../concepts/04-viking-uri.md#path-variables).

A directory name does not imply format support. Check [supported resource formats](../api/02-resources.md#resource-types) before import. Store unsupported artifact files through an enabled object-storage Connector, or wait for raw-file storage and parser support.

## Add and update parameter notes

1. Keep one stable root per logical source; before adding, you can use `list` (`ls`) or `stat` to check whether the target already exists.
2. For a new source, prefer `parent` to specify its parent directory. Use an exact `to` to synchronize a fixed source, or when you want to name the target directory directly; in general, `parent` is recommended.
3. When the parent directory is expected to be missing, use `--parent-auto-create` in the CLI or `create_parent=true` in the SDK and API; MCP's `parent` creates the directory automatically.
4. Use the watch mechanism for remote sources that change and can be read again, to avoid manually importing duplicate copies.
5. `reason` should state the intent and purpose of adding the resource; add `tags` only when the request or system policy explicitly requires them.
6. The import returns the final URI, visibility, and task state; retrieve the resource only after processing completes.

Account administrators can set a default add directory with `user_config_defaults.add_targets.resource_uri`; an identity-level `add_targets.resource_uri` can override it. Explicit `to` or `parent` values always take precedence.

## Agent filing rules

1. Distinguish Resource, Memory, and Skill first.
2. Preserve the target space's existing layout; follow `RESOURCE_POLICY.md` when present.
3. Use `projects/<project>/` for a clear collaboration project; otherwise file by source or form.
4. When visibility is unclear, use the current identity's `viking://~/resources/inbox/` and report the final location.
5. Name entity collection directories for the domain; do not create unsupported categories.
6. Ask before writing when the target already contains the same logical source or the shared scope is unclear.
