import * as React from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { Textarea } from '#/components/ui/textarea'
import { parsePositiveMinutes } from '#/lib/watch-interval'

import type { UpdateWatchInput, WatchTask } from '../-lib/api'

type EditWatchDialogProps = {
  onOpenChange: (open: boolean) => void
  onSubmit: (input: UpdateWatchInput) => void
  pending: boolean
  watch: WatchTask | null
}

export function EditWatchDialog({
  onOpenChange,
  onSubmit,
  pending,
  watch,
}: EditWatchDialogProps) {
  const { t } = useTranslation('watchesPage')
  const [intervalValue, setIntervalValue] = React.useState('')
  const [reason, setReason] = React.useState('')
  const [instruction, setInstruction] = React.useState('')

  React.useEffect(() => {
    setIntervalValue(watch ? String(watch.watchInterval) : '')
    setReason(watch?.reason ?? '')
    setInstruction(watch?.instruction ?? '')
  }, [watch])

  const interval = parsePositiveMinutes(intervalValue)
  const input = getWatchUpdateInput(watch, interval, reason, instruction)
  const canSubmit = interval !== null && Object.keys(input).length > 0

  return (
    <Dialog open={Boolean(watch)} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('editDialog.title')}</DialogTitle>
          <DialogDescription className="break-all font-mono text-xs">
            {watch?.toUri}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="watch-edit-interval">
              {t('editDialog.interval')}
            </Label>
            <Input
              id="watch-edit-interval"
              type="number"
              min="1"
              step="1"
              value={intervalValue}
              onChange={(event) => setIntervalValue(event.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {t('editDialog.intervalHint')}
            </p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="watch-edit-reason">{t('editDialog.reason')}</Label>
            <Textarea
              id="watch-edit-reason"
              placeholder={t('editDialog.reasonPlaceholder')}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="watch-edit-instruction">
              {t('editDialog.instruction')}
            </Label>
            <Textarea
              id="watch-edit-instruction"
              placeholder={t('editDialog.instructionPlaceholder')}
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
            />
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={() => onOpenChange(false)}
          >
            {t('cancel')}
          </Button>
          <Button
            type="button"
            disabled={!canSubmit || pending}
            onClick={() => canSubmit && onSubmit(input)}
          >
            {t('save')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function getWatchUpdateInput(
  watch: WatchTask | null,
  watchInterval: number | null,
  reason: string,
  instruction: string,
): UpdateWatchInput {
  if (!watch || watchInterval === null) return {}

  const input: UpdateWatchInput = {}
  if (watchInterval !== watch.watchInterval) input.watchInterval = watchInterval
  if (reason !== watch.reason) input.reason = reason
  if (instruction !== watch.instruction) input.instruction = instruction
  return input
}
