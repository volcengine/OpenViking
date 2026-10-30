import type { TFunction } from 'i18next'

export function localizeSkippedCommit(
  reason: string,
  t: TFunction<'tasksPage'>,
): string {
  return t(
    reason === 'no_messages'
      ? 'retry.noPendingMessages'
      : 'retry.commitSkipped',
  )
}
