import { OV_PATH_BUILDERS, OV_PATHS } from '../../../client/paths.js';
import { isOvClientError } from '../../../client/errors.js';
import type {
  OvClient,
  OvIdentityValue,
  OvRequestOptions,
} from '../../../client/types.js';
import type {
  FileBrowserAclContext,
  FileBrowserAclPrincipalPage,
  FileBrowserAclReport,
} from '../acl-types.js';
import { isLevel, parseReport } from './acl-model.js';

export const readAcl = async (
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) =>
  parseReport(
    await client.request({
      method: 'GET',
      path: OV_PATHS.acl,
      query: { uri },
      options,
    }),
    uri,
  );

export async function readPermission(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) {
  const stat = await client.request<{ permission?: unknown }>({
    method: 'GET',
    path: OV_PATHS.fsStat,
    query: { uri },
    options,
  });
  if (stat.permission !== undefined) {
    if (!isLevel(stat.permission))
      throw new TypeError('Unknown resource permission');
    return stat.permission;
  }
  // A successful manager-only endpoint proves manage. 403 does not distinguish
  // read from write, so retain an unknown level instead of guessing either one.
  try {
    await readAcl(client, uri, options);
    return 'manage' as const;
  } catch (error) {
    if (isOvClientError(error) && error.statusCode === 403) return undefined;
    throw error;
  }
}

export async function writeAcl(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
  change?: Pick<FileBrowserAclReport, 'direct_entries'> & {
    mode: 'inherit' | 'restricted';
  },
) {
  const value = await client.request({
    method: change ? 'PUT' : 'DELETE',
    path: OV_PATHS.acl,
    ...(change
      ? { body: { uri, acl_mode: change.mode, entries: change.direct_entries } }
      : { query: { uri } }),
    options,
  });
  return parseReport(value, uri);
}

export async function identityValue(value: OvIdentityValue | undefined) {
  return (typeof value === 'function' ? await value() : value)?.trim() ?? '';
}

export async function listPrincipals(
  query: { type: 'user' | 'group'; keyword: string; page: number },
  context: FileBrowserAclContext,
  accountId: OvIdentityValue | undefined,
): Promise<FileBrowserAclPrincipalPage> {
  const account = await identityValue(accountId);
  if (!account) throw new TypeError('Principal listing requires an account ID');
  if (query.type === 'group') {
    const groups = await context.client.request<{ group_id: string }[]>({
      method: 'GET',
      path: OV_PATH_BUILDERS.accountGroups(account),
      options: context.requestOptions,
    });
    return {
      principals: groups.map((group) => `group:${group.group_id}`),
      hasMore: false,
    };
  }
  const result = await context.client.request<{
    users: { user_id: string }[];
    total: number;
  }>({
    method: 'GET',
    path: OV_PATH_BUILDERS.accountUsers(account),
    query: {
      query: query.keyword,
      page: query.page,
      limit: 20,
      include_summary: true,
      include_credentials: false,
    },
    options: context.requestOptions,
  });
  return {
    principals: result.users.map((user) => `user:${user.user_id}`),
    hasMore: query.page * 20 < result.total,
  };
}
