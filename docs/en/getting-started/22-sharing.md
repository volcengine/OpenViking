# Knowledge sharing

Personal memories help agents retain a user's working context. When colleagues need project agreements or reusable experience, the memory owner can select the relevant knowledge, publish it as shared sources, and let other members' agents retrieve, read, and cite it.

This walkthrough follows Alice sharing Cape project review conventions with Bob: **select memories → prepare content → publish with permissions → retrieve and use as Bob → maintain updates**. It also covers delivery across teams.

## Choose a sharing method

| Scenario | Method |
| --- | --- |
| Share sources within an account | Publish under `viking://resources/...` and grant users or groups access through ACL. |
| Share personal experience within an account | The owner selects memory content and publishes it as shared sources. |
| Deliver knowledge between accounts | The source owner prepares deliverable content; the recipient imports it with their own credentials and assigns destination permissions. |
| Accumulate a team memory across users | The application coordinates knowledge selection, publishing, and updates; there is no switch that automatically merges private memories across users. |

Personal and Peer memory directories do not accept shared-source ACL. Published content is maintained as shared sources, separately from the original memories.

## Prerequisites

Complete [Authentication](17-authentication.md), preparing ordinary users Alice and Bob and their connections. Follow [Access control](21-access-control.md) to enable ACL for `acme` and create `viking://resources/team-knowledge`, with Alice as `manage` and Bob as `read`. If you tried revocation, Alice first restores Bob's `read` grant.

Alice needs a project memory to share. For a first walkthrough, use Alice's connection to complete the save-background and inspect-memory steps in [Memory usage](09-memory.md), producing the Cape conventions. This page does not require VikingBot; an agent with CLI access can perform the final question-answering step.

Imports, retrieval, and agent answers use the corresponding models. Each member uses their own credentials, with responsibilities divided as follows:

| Role | Responsibility |
| --- | --- |
| Account administrator | Enable ACL, create the shared directory, and assign initial grants. |
| Alice or her agent | Read Alice's memories, prepare a draft, publish, and maintain it. |
| Bob or his agent | Retrieve authorized sources, read the originals, and use them in tasks. |

## Alice selects memories

Use Alice's own connection in her terminal or agent:

```bash
ov config switch acme-alice
ov find "Cape project review conventions" --uri viking://~/memories
```

Read relevant results and identify what to share:

```bash
ov read "<memory-uri>"
```

Alice can give her agent this task:

```text
Prepare Cape project review conventions that I can share with Bob from my memories.
Read the originals, distinguish confirmed requirements from open questions, and draft Markdown.
Include sources for my review. Let me confirm the draft before publishing it to shared sources.
```

Alice confirms the content to publish. The shared document should contain understandable facts or cite sources Bob can read. Alice can retain private memory URIs in her personal review notes.

## Alice prepares a draft

For the Cape example, verify the memories and create `cape-review.md` locally:

```markdown
# Cape project review conventions

## Review requirements

- Discuss two risks in each review.
- Use three Chinese bullet points for the summary.
- Name the deliverable cape-review.csv.

## Open information

- The specific risks for this review have not been provided.

## Maintenance

- Maintainer: Alice
- Scope: Cape project reviews
```

This document corresponds to the sample input. For a real project, replace it with facts from the memories that Alice has confirmed, retaining the scope and open questions.

## Alice publishes sources

Choose a publishing method based on where the content is stored. Copy existing sources directly on the server, or import a newly prepared local draft with `add-resource`. Both methods use Alice's connection and publish to the directory already authorized for Bob.

### Copy existing sources

If the content is already in Alice's personal source directory, confirm that its text and references are suitable for sharing, then copy it without downloading it. This example assumes the source is `viking://~/resources/cape-review/cape-review.md`; replace it with the actual URI.

Alice needs read access to the source and `write` or `manage` permission on the target directory. Create a new publication directory under `team-knowledge`, then copy the file:

```bash
ov config switch acme-alice
ov read viking://~/resources/cape-review/cape-review.md
ov mkdir viking://resources/team-knowledge/cape-review
ov cp viking://~/resources/cape-review/cape-review.md \
  viking://resources/team-knowledge/cape-review/cape-review.md -o json
ov read viking://resources/team-knowledge/cape-review/cape-review.md
```

Here, `~` resolves to Alice, the current connection's user. The new shared source inherits target directory permissions, allowing Bob to retrieve and read it as described below. Alice's original remains in her personal directory.

If the entire source directory is suitable for sharing, use the following command instead of `mkdir` and the single-file copy above. The target parent `team-knowledge` must already exist; the target URI is the resulting directory location:

```bash
ov cp -r viking://~/resources/cape-review \
  viking://resources/team-knowledge/cape-review -o json
ov tree viking://resources/team-knowledge/cape-review
```

Copying includes existing vector records. Target parent summaries may refresh asynchronously: `semantic_status: queued` means the refresh is queued; `failed` indicates that `semantic_error` needs attention. Have Bob retrieve and read the published content to confirm access. See the [filesystem API](../api/03-filesystem.md#cp) for details.

The result is an independent copy; later source edits do not synchronize automatically. Copying to an existing file replaces its content. Directory copies merge into existing directories and retain files unique to the target. Use a new path for the first publication. Before updating, review the target content and any private URIs in the body that need replacing.

### Import a local draft

For the local Markdown draft prepared above, publish from its directory to a new path:

```bash
ov add-resource ./cape-review.md \
  --to viking://resources/team-knowledge/cape-review --wait
```

The publication inherits `team-knowledge` permissions. After processing completes, expand the directory and read the actual source URI:

```bash
ov tree viking://resources/team-knowledge/cape-review
ov read "<shared-source-uri>"
```

Retain that URI for Bob and future updates. If waiting times out, check the returned task ID with `ov task status <task_id>`. Confirm completion before Bob uses it.

## Bob retrieves and uses it

Bob retrieves with his own connection:

```bash
ov config switch acme-bob
ov find "What review format and deliverable filename does the Cape project use?" \
  --uri viking://resources/team-knowledge/cape-review
```

Read the shared source URI from the results and check the content. Then give Bob's agent this task:

```text
Use my ov CLI connection to retrieve sources under viking://resources/team-knowledge/cape-review.
Read the relevant originals and prepare a review outline following Cape conventions.
State the summary format, number of risks, and deliverable filename, citing full shared-source URIs.
Leave placeholders for specific risks that have not been provided.
```

The agent needs CLI access and must include the shared directory in its retrieval workflow. Access grants allow reading; the agent still needs to retrieve and read the content to use it in an answer.

| Check | Expected result |
| --- | --- |
| Identity | Bob's `ov health -o json` reports `acme` / `bob`. |
| Source | Results come from shared sources under `team-knowledge/cape-review`. |
| Answer | Two risks, three Chinese summary bullets, and `cape-review.csv`. |
| Unknown details | Specific risks remain placeholders or are identified as unspecified. |
| Private boundary | Bob still cannot read Alice's original private memory directory. |

## Maintain the publication

Alice maintains the content by default. For sources published by server-side copy, review the updated source and repeat the same file copy command. This replaces the shared copy's content while preserving target permissions.

For a publication from a local draft, edit `cape-review.md`, then update the source URI recorded earlier:

```bash
ov config switch acme-alice
ov write "<shared-source-uri>" --from-file ./cape-review.md --mode replace --wait
```

This replaces the complete file and reprocesses it. Check that the local file includes all content to retain before publishing. After completion, Bob retrieves and reads again to verify the updated conventions.

Changes to Alice's personal memories do not automatically update the publication. For joint editing, Alice can grant Bob `write`; revoke access when collaboration ends, as described in [Access control](21-access-control.md). Copies Bob already downloaded or saved require separate handling.

## Deliver across accounts

If Bob belongs to another account, such as `beta`, acme's ACL cannot grant access directly to beta's users. `user:bob` identifies Bob only within the current account.

Alice can deliver the approved `cape-review.md` to beta's source maintainer through an agreed channel. Beta's administrator follows [Access control](21-access-control.md) to enable ACL **in beta**, create a restricted shared directory, and authorize its intended members. Replace the example Account ID and user IDs with beta's actual members.

With destination write access, beta's maintainer imports the received file using their own connection:

```bash
ov config switch beta-admin
ov add-resource ./cape-review.md \
  --to viking://resources/team-knowledge/cape-review --wait
```

`beta-admin` is the destination connection created in [Account](18-accounts.md). Beta independently manages its directory, members, and grants. After import, destination members retrieve and verify the content. Each account retains its own copy; later updates require another delivery or application-managed synchronization.

For bulk migration, see [Import and export](15-transfer.md). Verify destination identities, permissions, and source links afterward. If subteams need to maintain one shared copy over time, put collaborators in the same account and use groups and ACL to separate access scopes.

## Next steps

Repeat the workflow with a real project agreement and check that Bob's agent finds and uses the shared content. See [Resource search](08-resources.md) for a broader source-based answering workflow.
