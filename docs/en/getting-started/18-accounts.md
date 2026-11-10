# Account

An Account is a team or tenant space in OpenViking. One service can host multiple accounts, each with its own users, sources, memories, and sessions.

Use separate accounts for teams, customers, or businesses that need distinct data boundaries. Members of one team collaborate within an account; use [ACL](21-access-control.md) for finer source permissions within that team.

## Understand the boundary

| Scope | Content | Sharing |
| --- | --- | --- |
| Account | Team users and data | Isolated from other accounts |
| User | Personal memories, sources, and sessions | Accessed by the user; selected knowledge can be published as shared sources |
| Shared sources | `viking://resources/...` | Shared within the current account, with optional ACL restrictions |

Two accounts can use the same URI, such as `viking://resources/team-check`. The request credential selects the account; there is no need to add an Account ID to the URI. This is logical data isolation. See [Account configuration](../api/08-admin.md) for dedicated storage or model settings.

## Prerequisites

Complete [Authentication](17-authentication.md) to create `acme`, `team-admin`, Alice, and Bob. The platform administrator has a Root key and has set `OV_URL` and `ROOT_KEY` in their terminal. This walkthrough adds a `beta` account.

Platform administrators create accounts; account administrators manage their own members. The checks below create empty directories without calling a generation model.

## Create another account

The platform administrator creates `beta` and its first administrator:

```bash
curl -sS -X POST "$OV_URL/api/v1/admin/accounts" \
  -H "X-API-Key: $ROOT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"account_id":"beta","admin_user_id":"beta-admin"}'
```

Save `result.user_key` and deliver it to beta-admin. That administrator uses `ov config` to save a connection named `beta-admin`, with the same service address and their own Admin key.

## Create content in acme

Alice creates a new shared directory using her acme connection:

```bash
ov config switch acme-alice
ov health -o json
ov mkdir viking://resources/team-check
ov ls viking://resources
```

Check that the identity belongs to `acme` and `team-check` appears in the directory listing. Choose a fresh name if it already exists.

## Check from beta

Beta-admin uses their own connection to inspect the same URI:

```bash
ov config switch beta-admin
ov health -o json
ov stat viking://resources/team-check
```

Confirm that the active account is `beta`. If beta has no directory with that name, the command should report it as missing; the directory created in acme is not visible here.

Beta-admin can then create a directory with the same name:

```bash
ov mkdir viking://resources/team-check
ov ls viking://resources
```

Creation should succeed. Each account now has its own `team-check`, and subsequent changes apply within the respective account.

## Select a team connection

In API key mode, each user credential is bound to an Account and User. To access another team, obtain an identity and credential from that team's administrator and save a separate connection.

Applications should map each signed-in user to the appropriate OpenViking connection. Passing `--account` cannot turn an existing user key into another team's identity. See [Authentication](../guides/04-authentication.md) for deployments that pass identity through a trusted upstream.

## Check the result

| Check | Expected result |
| --- | --- |
| Alice's health response | `account_id` is `acme` |
| Beta-admin's health response | `account_id` is `beta` |
| Before creating the directory in beta | Acme's `team-check` is not visible |
| After creating it in beta | Each account has an independent directory |

If results differ, use `ov config show` and `ov health -o json` to check the active service, user, and account. Default local mode without authentication is not suitable for verifying multiple-user identity isolation.

## Next steps

Continue to [User](19-users.md) to check personal data boundaries within one account. See [Knowledge sharing](22-sharing.md) to deliver sources across accounts.
