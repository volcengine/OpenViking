import { OV_PATHS, type OvPath } from './paths.js';
import type { OvQueryValue } from './types.js';

export interface QueryScope {
  service: string;
  credentials: RequestCredentials;
  identity: string;
  transport: string;
}

export const queryKeys = {
  scope: (scope: QueryScope) => ['openviking', scope] as const,
  directory: (
    scope: QueryScope,
    params: Readonly<Record<string, OvQueryValue>>,
  ) => [...queryKeys.scope(scope), OV_PATHS.fsLs, params] as const,
  search: (scope: QueryScope, path: OvPath, body: unknown) =>
    [...queryKeys.scope(scope), path, body] as const,
};
