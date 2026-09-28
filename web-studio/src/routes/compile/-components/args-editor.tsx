import { useEffect, useRef, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Textarea } from '#/components/ui/textarea'
import { parseArgs } from '../-lib/commands'

type Row = { id: number; key: string; type: string; value: string }
const TYPES = ['string', 'number', 'boolean', 'json'] as const
function decode(value: string): Row[] {
  return Object.entries(parseArgs(value) ?? {}).map(([key, v], id) => ({
    id,
    key,
    type:
      typeof v === 'string'
        ? 'string'
        : typeof v === 'number'
          ? 'number'
          : typeof v === 'boolean'
            ? 'boolean'
            : 'json',
    value: typeof v === 'string' ? v : JSON.stringify(v),
  }))
}
export function ArgsEditor({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  const { t } = useTranslation('compile')
  const [mode, setMode] = useState<'fields' | 'json'>(() => {
    try {
      decode(value)
      return 'fields'
    } catch {
      return 'json'
    }
  })
  const [rows, setRows] = useState<Row[]>(() => {
    try {
      return decode(value)
    } catch {
      return []
    }
  })
  const [error, setError] = useState('')
  const emitted = useRef(value)
  useEffect(() => {
    if (value === emitted.current) return
    emitted.current = value
    try {
      setRows(decode(value))
      setError('')
    } catch {
      setMode('json')
    }
  }, [value])
  function emit(next: string) {
    emitted.current = next
    onChange(next)
  }
  function edit(next: Row[]) {
    setRows(next)
    try {
      const entries: [string, unknown][] = []
      const keys = new Set<string>()
      for (const row of next) {
        if (!row.key.trim()) throw Error(t('argKeyRequired'))
        if (keys.has(row.key)) throw Error(t('argDuplicate'))
        keys.add(row.key)
        let v: unknown = row.value
        if (row.type === 'number') {
          if (!row.value.trim() || !Number.isFinite(Number(row.value)))
            throw Error(t('argNumberError'))
          v = Number(row.value)
        } else if (row.type === 'boolean') v = row.value === 'true'
        else if (row.type === 'json') {
          try {
            v = JSON.parse(row.value)
          } catch {
            throw Error(t('argJsonError'))
          }
        }
        entries.push([row.key, v])
      }
      setError('')
      emit(
        next.length ? JSON.stringify(Object.fromEntries(entries), null, 2) : '',
      )
    } catch (cause) {
      setError((cause as Error).message)
      // An invalid draft must never submit the previous valid parameter values.
      emit(' ' + JSON.stringify(next) + '\n')
    }
  }
  function patch(id: number, change: Partial<Row>) {
    edit(rows.map((row) => (row.id === id ? { ...row, ...change } : row)))
  }
  return (
    <div className="mt-4 space-y-3">
      <div
        className="flex items-center gap-1 rounded-lg bg-muted/40 p-1"
        role="group"
        aria-label={t('argMode')}
      >
        <Button
          type="button"
          size="sm"
          variant={mode === 'fields' ? 'secondary' : 'ghost'}
          aria-pressed={mode === 'fields'}
          onClick={() => {
            try {
              setRows(decode(value))
              setError('')
              setMode('fields')
            } catch {
              setError(t('argsError'))
            }
          }}
        >
          {t('argFields')}
        </Button>
        <Button
          type="button"
          size="sm"
          variant={mode === 'json' ? 'secondary' : 'ghost'}
          aria-pressed={mode === 'json'}
          disabled={mode === 'fields' && !!error}
          onClick={() => setMode('json')}
        >
          {t('argRaw')}
        </Button>
      </div>
      {mode === 'json' ? (
        <Textarea
          id="compile-args"
          aria-label={t('argRaw')}
          aria-describedby="args-editor-error"
          value={value}
          rows={6}
          className="font-mono text-xs"
          placeholder={JSON.stringify({ key: 'value' }, null, 2)}
          onChange={(e) => {
            emit(e.target.value)
            try {
              parseArgs(e.target.value)
              setError('')
            } catch {
              setError(t('argsError'))
            }
          }}
        />
      ) : (
        <div id="compile-args" tabIndex={-1} className="space-y-3">
          {!rows.length && (
            <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
              {t('argEmpty')}
            </p>
          )}
          {rows.map((row, index) => (
            <div
              key={row.id}
              className="grid grid-cols-[1fr_auto] gap-2 rounded-lg border p-3 sm:grid-cols-[minmax(0,1fr)_110px_minmax(0,1.5fr)_auto]"
            >
              <Input
                aria-label={`${t('argKey')} ${index + 1}`}
                placeholder={t('argKey')}
                value={row.key}
                onChange={(e) => patch(row.id, { key: e.target.value })}
              />
              <select
                aria-label={`${t('argType')} ${index + 1}`}
                className="h-9 rounded-md border bg-background px-2 text-sm"
                value={row.type}
                onChange={(e) =>
                  patch(row.id, {
                    type: e.target.value,
                    value: e.target.value === 'boolean' ? 'false' : row.value,
                  })
                }
              >
                {TYPES.map((type) => (
                  <option key={type} value={type}>
                    {t(`argTypes.${type}`)}
                  </option>
                ))}
              </select>
              {row.type === 'boolean' ? (
                <select
                  aria-label={`${t('argValue')} ${index + 1}`}
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  value={row.value}
                  onChange={(e) => patch(row.id, { value: e.target.value })}
                >
                  <option value="true">{String(true)}</option>
                  <option value="false">{String(false)}</option>
                </select>
              ) : (
                <Input
                  aria-label={`${t('argValue')} ${index + 1}`}
                  placeholder={t('argValue')}
                  value={row.value}
                  onChange={(e) => patch(row.id, { value: e.target.value })}
                />
              )}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`${t('argRemove')} ${index + 1}`}
                onClick={() => edit(rows.filter((v) => v.id !== row.id))}
              >
                <X className="size-4" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() =>
              edit([
                ...rows,
                {
                  id: Math.max(-1, ...rows.map((r) => r.id)) + 1,
                  key: '',
                  type: 'string',
                  value: '',
                },
              ])
            }
          >
            <Plus className="size-4" />
            {t('argAdd')}
          </Button>
        </div>
      )}
      {error && (
        <p
          id="args-editor-error"
          role="alert"
          className="text-xs text-destructive"
        >
          {error}
        </p>
      )}
    </div>
  )
}
