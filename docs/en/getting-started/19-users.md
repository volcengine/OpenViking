# User

A User is a data-owning identity within an Account. OpenViking uses it to separate personal memories, private sources, and sessions. Alice and Bob can each retain long-term context while using shared team sources.

A User is the identity accessing the service. An application can map it to a person or a separate application identity. Multiple agents using one User key share that user's space; use different users when their personal data needs to remain separate.

## Understand user space

Alice's directory looks like this:

```text
viking://user/alice/
├── memories/    # Alice's long-term memories
├── resources/   # Alice's private sources
├── skills/      # Alice's private skills
├── peers/       # Alice's interaction counterparts
└── sessions/    # Alice's conversations
```

`viking://~/` aliases the current user's home directory. It expands to `viking://user/alice/` for Alice and `viking://user/bob/` for Bob. Shared sources live under `viking://resources/` in the same account.

## Prerequisites

Complete [Authentication](17-authentication.md), creating Alice and Bob as ordinary users in `acme` and saving `acme-alice` and `acme-bob` connections. This example creates an empty private directory and a session without triggering memory extraction.

## Create private content

Alice activates her connection, checks her identity, and creates a directory:

```bash
ov config switch acme-alice
ov health -o json
ov mkdir viking://~/resources/personal-check
ov ls viking://~/resources
```

The listing should include `personal-check`. Create a session owned by Alice with automatic commits disabled:

```bash
ov session new --session-id alice-user-check --no-auto-commit
ov session add-message alice-user-check --role user --content "This is Alice's personal project discussion."
ov session get alice-user-check
```

Check that the session contains the message. This saves conversation content; committing starts memory extraction. See [Memory usage](09-memory.md) for that workflow.

## Check as Bob

Bob lists his home directory with his own connection:

```bash
ov config switch acme-bob
ov ls viking://~/resources
```

This lists Bob's private sources. Try accessing the directory Alice just created:

```bash
ov ls viking://user/alice/resources/personal-check
```

Access should be denied. Account membership enables collaboration while personal spaces remain separated by User. To share selected knowledge, Alice publishes it as shared sources, as described in [Knowledge sharing](22-sharing.md).

## Users and roles

The User identifies the data owner; the role determines management capabilities:

| User role | Main capabilities |
| --- | --- |
| USER | Personal data operations and authorized shared-source access |
| ADMIN | Their own user space, account member management, and management of shared sources in the account |

An administrator's ordinary data requests still use their own User identity. In API key mode, changing identity headers cannot switch an administrator into Alice or Bob. Platform ROOT manages accounts and other platform operations; its key is not used to read ordinary tenant data.

Applications can reuse a user identity across conversations to accumulate long-term memory. Session IDs distinguish conversations; Peers distinguish stable interaction counterparts under that user.

## Check the result

Alice can access her directory and session. Bob's home alias resolves to his own space, and he cannot read Alice's private directory. If both see the same personal space, check whether they are sharing a key.

## Next steps

Read [Peer](20-peers.md) to organize sources and memories for different counterparts under one user. See [Admin API](../api/08-admin.md) for registration, roles, and key rotation.
