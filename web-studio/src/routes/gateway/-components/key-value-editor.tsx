import * as React from 'react'
import { PlusIcon, Trash2Icon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'

type Row = { id: number; key: string; value: string }

type CommonProps = {
  keyPlaceholder: string
  valuePlaceholder: string
  /** Label of the add button; defaults to "Add". */
  addLabel?: string
  /** Names whose value is stored but never shown; a blank value keeps it. */
  storedKeys?: string[]
  /** Render values as password fields (header secrets). */
  maskValues?: boolean
  'aria-invalid'?: boolean
}

type TextProps = CommonProps & {
  numeric?: false
  value: Record<string, string>
  onChange: (value: Record<string, string>) => void
}

/** Number values; an empty or non-numeric entry is emitted as `NaN`. */
type NumberProps = CommonProps & {
  numeric: true
  value: Record<string, number>
  onChange: (value: Record<string, number>) => void
}

export type KeyValueEditorProps = TextProps | NumberProps

let nextRowId = 0

function toRows(record: Record<string, string | number>): Row[] {
  return Object.entries(record).map(([key, value]) => ({
    id: nextRowId++,
    key,
    value:
      typeof value === 'number' && Number.isNaN(value) ? '' : String(value),
  }))
}

function sameRecord(a: object, b: object): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/**
 * Editable name → value rows. Rows without a name are kept on screen but left
 * out of the value; when a name repeats, the last row wins and is flagged.
 */
export function KeyValueEditor(props: KeyValueEditorProps) {
  const { t } = useTranslation('contextGateway')
  const { storedKeys = [], maskValues } = props
  const [rows, setRows] = React.useState(() => toRows(props.value))
  const [synced, setSynced] = React.useState<object>(props.value)
  if (props.value !== synced) {
    // The parent replaced the value (load, reset): rebuild the rows.
    setSynced(props.value)
    setRows(toRows(props.value))
  }

  function emit<TValue>(
    onChange: (value: Record<string, TValue>) => void,
    record: Record<string, TValue>,
  ) {
    if (sameRecord(record, props.value)) return
    setSynced(record)
    onChange(record)
  }

  function update(next: Row[]) {
    setRows(next)
    const entries = next
      .filter((row) => row.key.trim())
      .map((row) => [row.key.trim(), row.value] as const)
    if (props.numeric) {
      const toNumber = (text: string) => (text.trim() ? Number(text) : NaN)
      emit(
        props.onChange,
        Object.fromEntries(entries.map(([key, text]) => [key, toNumber(text)])),
      )
    } else {
      emit(props.onChange, Object.fromEntries(entries))
    }
  }

  const change = (id: number, patch: Partial<Row>) =>
    update(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)))

  const lastIndex = new Map(rows.map((row, index) => [row.key.trim(), index]))

  return (
    <div className="grid gap-2">
      {rows.map((row, index) => {
        const name = row.key.trim()
        const duplicate = Boolean(name) && lastIndex.get(name) !== index
        const stored = storedKeys.includes(name) && !row.value
        return (
          <div key={row.id} className="grid gap-1">
            <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-2">
              <Input
                {...PLAIN_INPUT_PROPS}
                value={row.key}
                placeholder={props.keyPlaceholder}
                aria-label={props.keyPlaceholder}
                aria-invalid={duplicate || props['aria-invalid']}
                className="font-mono"
                onChange={(event) =>
                  change(row.id, { key: event.target.value })
                }
              />
              <Input
                {...PLAIN_INPUT_PROPS}
                type={
                  maskValues ? 'password' : props.numeric ? 'number' : 'text'
                }
                autoComplete={maskValues ? 'new-password' : 'off'}
                inputMode={props.numeric ? 'numeric' : undefined}
                value={row.value}
                placeholder={
                  stored ? t('field.storedSecret') : props.valuePlaceholder
                }
                aria-label={props.valuePlaceholder}
                aria-invalid={props['aria-invalid']}
                className="font-mono"
                onChange={(event) =>
                  change(row.id, { value: event.target.value })
                }
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={t('keyValue.remove')}
                className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                onClick={() =>
                  update(rows.filter((other) => other.id !== row.id))
                }
              >
                <Trash2Icon />
              </Button>
            </div>
            {duplicate ? (
              <p className="text-xs text-amber-600 dark:text-amber-300">
                {t('keyValue.duplicate')}
              </p>
            ) : null}
          </div>
        )
      })}
      <div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            setRows([...rows, { id: nextRowId++, key: '', value: '' }])
          }
        >
          <PlusIcon />
          {props.addLabel ?? t('keyValue.add')}
        </Button>
      </div>
    </div>
  )
}
