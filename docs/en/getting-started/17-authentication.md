# Authentication

An API key identifies the caller and determines which operations they can perform. For team use, a platform administrator configures a Root key, then creates separate credentials for team administrators and ordinary users.

This walkthrough enables API key authentication, creates the `acme` account with `team-admin` as its first administrator, and registers Alice and Bob as ordinary users. The other team management pages reuse these identities.

## Prerequisites

Use a self-hosted service whose configuration you can edit and restart. Prepare the `ov` CLI, curl, and an existing server configuration. Keep the model and storage settings from the [quickstart](02-quickstart.md); the JSON below shows only fields to merge into that configuration.

Volcengine manages authentication and accounts for its cloud service. Obtain credentials through its console; the Root key setup below applies to self-hosted services.

| Identity | Credential | Purpose |
| --- | --- | --- |
| Platform administrator, ROOT | `server.root_api_key` | Platform operations such as creating and managing accounts |
| Account administrator, ADMIN | `user_key` returned when creating the administrator | Manage users in that account, the administrator's own data, and shared sources |
| Ordinary user, USER | `user_key` returned when creating the user | Access personal data and authorized shared sources |

In API key mode, the server derives the account, user, and role from the credential. A Root key has no tenant user binding; use a User/Admin key for ordinary source, memory, and session operations.

## Configure a Root key

The platform administrator first backs up the active `ov.conf`. Generate a random key in a local terminal, for example:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Store the generated value securely and enter it in the server configuration. Keep the existing model, storage, and other server settings:

```json
{
  "server": {
    "host": "127.0.0.1",
    "auth_mode": "api_key",
    "root_api_key": "<random-root-key>"
  }
}
```

Restart using the original configuration path and startup options. A local service using the default path can run:

```bash
openviking-server --config ~/.openviking/ov.conf
```

Retain `--with-bot` if you also use VikingBot. See [Deployment](../guides/03-deployment.md) for network addresses, HTTPS, and reverse proxies.

After authentication is enabled, clients need the appropriate credentials. Existing data stays in its original storage and account; creating a team does not migrate data from the previous default account.

## Create an administrator

The platform administrator runs this step. The examples use bash or zsh on macOS / Linux. Set `OV_URL` to your service address. After running `read`, paste the Root key configured above and press Enter; input is hidden:

```bash
export OV_URL="http://127.0.0.1:1933"
read -r -s ROOT_KEY
export ROOT_KEY
```

Create `acme` and its first administrator, `team-admin`:

```bash
curl -sS -X POST "$OV_URL/api/v1/admin/accounts" \
  -H "X-API-Key: $ROOT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"account_id":"acme","admin_user_id":"team-admin"}'
```

Check that the response has `status: ok` and save `result.user_key`. This is **team-admin's Admin key**, separate from the Root key. Deliver it securely to that administrator. The response contains credentials; restrict saved or forwarded copies to the intended user.

If `acme` already exists, continue with it. For a separate walkthrough, choose a new account name and replace the Account ID in subsequent commands.

## Create ordinary users

The team administrator opens the CLI connection wizard:

```bash
ov config
```

Add a Custom connection named `acme-admin`, using the service address and team-admin's Admin key. Save and activate it, check the connection, and register two ordinary users:

```bash
ov health
ov admin register-user acme alice --role user
ov admin register-user acme bob --role user
```

Each registration returns that user's `user_key`. Deliver the corresponding credential to Alice or Bob. They save their own connections as `acme-alice` and `acme-bob`. See [CLI basics](05-cli-setup.md) for connection setup.

To add another administrator, register a user with `--role admin`. See [Admin API](../api/08-admin.md) for role management. An account administrator can manage their team's members without a Root key.

## Verify the identity

In Alice's terminal, activate her connection:

```bash
ov config switch acme-alice
ov health -o json
ov ls viking://~/
```

The health response should report `account_id: acme`, `user_id: alice`, and `role: user`; listing Alice's home directory should succeed. With team-admin's connection, the identity should be `team-admin` with role `admin`.

Each person can use these connection names on their own computer. If one operator checks several identities, save the corresponding profiles and switch before each group of operations. Changing `--user` or `--account` does not change the identity bound to an API key.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| An old connection fails after enabling authentication | Add that user's User/Admin key through `ov config`. |
| Root can create an account but cannot read sources | Use a User/Admin key belonging to the account for data operations. |
| A user cannot register members | Switch to that account's administrator connection. |
| A new team cannot see previous data | Check the original account and choose publishing or migration in [Knowledge sharing](22-sharing.md). |

After platform operations, run `unset ROOT_KEY` to clear the variable from the current terminal. See [Authentication](../guides/04-authentication.md) for key rotation and other authentication modes.

## Next steps

Continue to [Account](18-accounts.md) to create a second team and verify isolation.
