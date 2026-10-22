import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, expectTypeOf, it } from 'vitest'

import type {
  AccountPage,
  AuditPage,
  CursorPage,
  ErrorResponse,
  ExperienceTrajectoryPage,
  FilesystemItem,
  GetAdminAccountIdUsersData,
  GetAdminAccountIdUsersErrors,
  GetAdminAccountIdUsersResponses,
  GetAdminAccountsData,
  GetAdminAccountsErrors,
  GetAdminAccountsResponses,
  GetAgentEvolutionExperiencesTrajectoriesErrors,
  GetAgentEvolutionExperiencesTrajectoriesResponses,
  GetConsoleAuditErrors,
  GetConsoleAuditResponses,
  GetFsLsErrors,
  GetFsLsResponses,
  GetFsTreeErrors,
  GetFsTreeResponses,
  GetSessionsErrors,
  GetSessionsResponses,
  GetSkillsErrors,
  GetSkillsResponses,
  GetTasksData,
  GetTasksErrors,
  GetTasksResponses,
  GetWatchesErrors,
  GetWatchesResponses,
  OffsetPage,
  PageResponse,
  SessionListItem,
  SkillPage,
  TaskListItem,
  UserPage,
  WatchTaskPublic,
} from './types.gen'

const generatedTypesSource = readFileSync(
  fileURLToPath(new URL('./types.gen.ts', import.meta.url)),
  'utf8',
)

const paginationContractTypes = [
  'GetAdminAccounts',
  'GetAdminAccountIdUsers',
  'GetFsLs',
  'GetFsTree',
  'GetConsoleAudit',
  'GetSessions',
  'GetTasks',
  'GetSkills',
  'GetWatches',
  'GetAgentEvolutionExperiencesTrajectories',
]

describe('generated pagination contract types', () => {
  it('does not regress target list endpoints to unknown 200 or validation 422', () => {
    for (const typeName of paginationContractTypes) {
      expect(generatedTypesSource).toMatch(
        new RegExp(
          `export type ${typeName}Responses = \\{[^}]*200: PageResponse<`,
        ),
      )
      expect(generatedTypesSource).toMatch(
        new RegExp(
          `export type ${typeName}Errors = \\{[^}]*400: ErrorResponse;`,
        ),
      )
      expect(generatedTypesSource).not.toMatch(
        new RegExp(
          `export type ${typeName}Responses = \\{[^}]*200: unknown;`,
        ),
      )
      expect(generatedTypesSource).not.toMatch(
        new RegExp(
          `export type ${typeName}Errors = \\{[^}]*422: HttpValidationError;`,
        ),
      )
    }
  })

  it('types target list responses as concrete page envelopes', () => {
    expectTypeOf<GetAdminAccountsResponses[200]>().toEqualTypeOf<
      PageResponse<AccountPage>
    >()
    expectTypeOf<GetAdminAccountIdUsersResponses[200]>().toEqualTypeOf<
      PageResponse<UserPage>
    >()
    expectTypeOf<GetFsLsResponses[200]>().toEqualTypeOf<
      PageResponse<OffsetPage<FilesystemItem>>
    >()
    expectTypeOf<GetFsTreeResponses[200]>().toEqualTypeOf<
      PageResponse<OffsetPage<FilesystemItem>>
    >()
    expectTypeOf<GetConsoleAuditResponses[200]>().toEqualTypeOf<
      PageResponse<AuditPage>
    >()
    expectTypeOf<GetSessionsResponses[200]>().toEqualTypeOf<
      PageResponse<OffsetPage<SessionListItem>>
    >()
    expectTypeOf<GetTasksResponses[200]>().toEqualTypeOf<
      PageResponse<CursorPage<TaskListItem>>
    >()
    expectTypeOf<GetSkillsResponses[200]>().toEqualTypeOf<
      PageResponse<SkillPage>
    >()
    expectTypeOf<GetWatchesResponses[200]>().toEqualTypeOf<
      PageResponse<OffsetPage<WatchTaskPublic>>
    >()
    expectTypeOf<
      GetAgentEvolutionExperiencesTrajectoriesResponses[200]
    >().toEqualTypeOf<PageResponse<ExperienceTrajectoryPage>>()
  })

  it('types pagination contract errors as runtime 400 envelopes', () => {
    expectTypeOf<GetAdminAccountsErrors>().toEqualTypeOf<{
      400: ErrorResponse
    }>()
    expectTypeOf<GetAdminAccountIdUsersErrors>().toEqualTypeOf<{
      400: ErrorResponse
    }>()
    expectTypeOf<GetFsLsErrors>().toEqualTypeOf<{ 400: ErrorResponse }>()
    expectTypeOf<GetFsTreeErrors>().toEqualTypeOf<{ 400: ErrorResponse }>()
    expectTypeOf<GetConsoleAuditErrors>().toEqualTypeOf<{
      400: ErrorResponse
    }>()
    expectTypeOf<GetSessionsErrors>().toEqualTypeOf<{ 400: ErrorResponse }>()
    expectTypeOf<GetTasksErrors>().toEqualTypeOf<{ 400: ErrorResponse }>()
    expectTypeOf<GetSkillsErrors>().toEqualTypeOf<{ 400: ErrorResponse }>()
    expectTypeOf<GetWatchesErrors>().toEqualTypeOf<{ 400: ErrorResponse }>()
    expectTypeOf<
      GetAgentEvolutionExperiencesTrajectoriesErrors
    >().toEqualTypeOf<{ 400: ErrorResponse }>()
  })

  it('includes query fields added with the unified pagination contract', () => {
    expectTypeOf<NonNullable<GetAdminAccountsData['query']>>().toMatchTypeOf<{
      query?: string | null
      offset?: number
      limit?: number
    }>()
    expectTypeOf<
      NonNullable<GetAdminAccountIdUsersData['query']>
    >().toMatchTypeOf<{
      include_credentials?: boolean
      query?: string | null
      offset?: number
      limit?: number
    }>()
    expectTypeOf<NonNullable<GetTasksData['query']>>().toMatchTypeOf<{
      include_internal?: boolean
      cursor?: string | null
      limit?: number
      q?: string | null
    }>()
  })
})
