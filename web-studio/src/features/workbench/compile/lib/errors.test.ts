import { compileErrorKey } from '#/features/workbench/compile/lib/errors'
import { OvClientError } from '#/shared/api/ov-client/index'
import { expect, it } from 'vitest'

it.each([
  ['COMPILE_SCHEDULER_DISABLED', 'schedule.schedulerDisabled'],
  ['COMPILE_CREDENTIAL_INVALID', 'schedule.credentialInvalid'],
  ['COMPILE_CREDENTIAL_KEY_UNAVAILABLE', 'schedule.credentialKeyUnavailable'],
])('explains why a schedule save failed: %s', (reason, expected) => {
  expect(
    compileErrorKey(
      new OvClientError({
        code: 'FAILED_PRECONDITION',
        message: 'Schedule save rejected',
        details: { reason },
      }),
    ),
  ).toBe(expected)
})
