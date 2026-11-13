import type { OvClient, OvRequestOptions } from '../../client/types.js';

export type FileBrowserAclLevel = 'read' | 'write' | 'manage';
export interface FileBrowserAclEntry {
  principal: string;
  level: FileBrowserAclLevel;
}
export interface FileBrowserAclReport {
  uri: string;
  acl_mode: 'none' | 'inherit' | 'restricted';
  direct_entries: FileBrowserAclEntry[];
  inherited_entries: FileBrowserAclEntry[];
  effective_entries: FileBrowserAclEntry[];
}
export interface FileBrowserAclContext {
  client: OvClient;
  requestOptions: OvRequestOptions;
}
export interface FileBrowserAclPrincipalPage {
  principals: string[];
  hasMore: boolean;
}
/** Optional integration where a service has additional permission metadata. */
export interface FileBrowserAclOptions {
  /** 禁止授权修改，仍可查看。Prevent mutations while retaining the grant list. */
  readOnly?: boolean;
  /**
   * 自身有效权限；默认先读 fs/stat.access，缺失时仅探测管理权限。
   * Own effective level. Defaults to fs/stat.access, then a manage-only ACL probe.
   * No particular user ID is assumed to be an administrator.
   */
  resolvePermission?: (
    uri: string,
    context: FileBrowserAclContext,
  ) => Promise<FileBrowserAclLevel | undefined>;
  /**
   * 可选候选目录；默认调用原生账号用户/用户组接口，不读取密钥。
   * Optional principal directory; defaults to native account APIs without credentials.
   */
  listPrincipals?: (
    query: { type: 'user' | 'group'; keyword: string; page: number },
    context: FileBrowserAclContext,
  ) => Promise<FileBrowserAclPrincipalPage>;
  /** 保存成功后的通知；失败不触发。Notification after a successful ACL mutation. */
  onChange?: (report: FileBrowserAclReport) => void;
}

export interface FileBrowserAclLabels {
  title?: string;
  manage?: string;
  read?: string;
  write?: string;
  manageLevel?: string;
  readDescription?: string;
  writeDescription?: string;
  manageDescription?: string;
  effectivePermission?: string;
  addPrincipal?: string;
  search?: string;
  users?: string;
  groups?: string;
  allUsers?: string;
  administrator?: string;
  administratorDescription?: string;
  direct?: string;
  inherited?: string;
  remove?: string;
  add?: string;
  cancel?: string;
  confirm?: string;
  close?: string;
  retry?: string;
  loadMore?: string;
  loading?: string;
  saving?: string;
  saved?: string;
  loadFailed?: string;
  saveFailed?: string;
  principalsFailed?: string;
  empty?: string;
  noMatches?: string;
  restricted?: string;
  restore?: string;
  stopTitle?: string;
  stopDescription?: string;
  restoreTitle?: string;
  restoreDescription?: string;
  restoreRemoved?: string;
  readOnly?: string;
}
