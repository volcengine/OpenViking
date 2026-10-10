import * as React from 'react'
import { EyeIcon, EyeOffIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Textarea } from '#/components/ui/textarea'
import { Switch } from '#/components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { Field, FieldLabel } from '#/components/ui/field'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'
import type { ModelConfig } from '../-lib/config-file-api'

export type EditableModelKind = 'vlm' | 'embedding'

const CUSTOM_OPENAI = '__custom_openai'

export type FieldSpec = {
  key: string
  type?: 'secret' | 'number' | 'json' | 'toggle'
  options?: string[]
  required?: boolean
  min?: number
}
export const providers: Record<EditableModelKind, string[]> = {
  vlm: [
    'volcengine',
    'openai',
    'azure',
    'kimi',
    'glm',
    'litellm',
    'openai-codex',
  ],
  embedding: [
    'volcengine',
    'openai',
    'azure',
    'vikingdb',
    'jina',
    'ollama',
    'gemini',
    'voyage',
    'dashscope',
    'minimax',
    'cohere',
    'litellm',
    'local',
  ],
}
export function bindingFields(
  kind: EditableModelKind,
  provider: string,
): FieldSpec[] {
  const fields: FieldSpec[] = [
    { key: 'provider', options: providers[kind], required: true },
  ]
  fields.push({ key: 'model' })
  if (provider === 'vikingdb')
    fields.push(
      { key: 'ak', type: 'secret', required: true },
      { key: 'sk', type: 'secret', required: true },
      { key: 'host' },
      ...(kind === 'embedding' ? [{ key: 'region' }] : []),
    )
  else if (!['local', 'ollama', 'openai-codex'].includes(provider))
    fields.push({
      key: 'api_key',
      type: 'secret',
      required:
        provider !== 'litellm' &&
        !(kind === 'embedding' && provider === 'openai'),
    })
  if (provider !== 'vikingdb' && provider !== 'local')
    fields.push({
      key: 'api_base',
      required: provider === 'azure',
    })
  if (provider === 'azure') fields.push({ key: 'api_version' })
  return fields
}
function JsonField({
  value,
  onChange,
  readOnly,
  id,
  required,
}: {
  value: unknown
  onChange: (value: unknown) => void
  readOnly: boolean
  id: string
  required?: boolean
}) {
  const { t } = useTranslation('settings')
  const [text, setText] = React.useState(
    value === undefined || value === null ? '' : JSON.stringify(value, null, 2),
  )
  return (
    <Textarea
      id={id}
      readOnly={readOnly}
      required={required}
      rows={4}
      className="font-mono text-xs"
      value={text}
      onChange={(event) => {
        const input = event.currentTarget
        setText(input.value)
        try {
          const parsed = input.value.trim() ? JSON.parse(input.value) : null
          if (
            parsed !== null &&
            (typeof parsed !== 'object' || Array.isArray(parsed))
          )
            throw new Error('Expected JSON object')
          input.setCustomValidity('')
          onChange(parsed)
        } catch {
          input.setCustomValidity(t('models.invalidJsonObject'))
        }
      }}
    />
  )
}
export function ModelFields({
  fields,
  value,
  onChange,
  readOnly = false,
  prefix = 'model',
  inheritedModel,
}: {
  fields: FieldSpec[]
  value: ModelConfig
  onChange: (next: ModelConfig) => void
  readOnly?: boolean
  prefix?: string
  inheritedModel?: unknown
}) {
  const { t } = useTranslation('settings')
  const [visible, setVisible] = React.useState<Record<string, boolean>>({})
  const provider = String(value.provider || '').toLowerCase()
  const [customProvider, setCustomProvider] = React.useState(
    provider === 'openai' && Boolean(value.api_base),
  )
  const needsEmbeddingBase =
    fields.some((field) => field.key === 'provider') &&
    fields.some((field) => field.key === 'api_key' && !field.required) &&
    provider === 'openai' &&
    !value.api_key
  return (
    <>
      {fields.map((field) => {
        const id = `${prefix}-${field.key}`
        const current =
          field.key === 'provider' && typeof value.provider === 'string'
            ? provider
            : field.key === 'model'
              ? value.model || inheritedModel
              : value[field.key]
        const change = (next: unknown) =>
          onChange({ ...value, [field.key]: next })
        const label = t(`models.fields.${field.key}`)
        const isProvider = field.key === 'provider'
        const options = field.options?.map((option) => ({
          value: option,
          label: option,
        }))
        if (isProvider && options)
          options.push({
            value: CUSTOM_OPENAI,
            label: t('models.customProvider'),
          })
        return (
          <Field key={field.key} className="gap-2">
            <FieldLabel htmlFor={id} className="text-xs">
              {label}
            </FieldLabel>
            {field.options ? (
              <Select
                value={
                  isProvider && customProvider
                    ? CUSTOM_OPENAI
                    : typeof current === 'string'
                      ? current
                      : null
                }
                disabled={readOnly}
                required={field.required}
                items={options}
                onValueChange={(next) => {
                  if (isProvider) {
                    setCustomProvider(next === CUSTOM_OPENAI)
                    change(next === CUSTOM_OPENAI ? 'openai' : next)
                  } else change(next)
                }}
              >
                <SelectTrigger id={id} className="h-10 w-full">
                  <SelectValue placeholder={t('models.selectProvider')} />
                </SelectTrigger>
                <SelectContent align="start">
                  {options?.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : field.type === 'json' ? (
              <JsonField
                value={current}
                onChange={change}
                readOnly={readOnly}
                id={id}
              />
            ) : field.type === 'toggle' ? (
              <Switch
                id={id}
                checked={current === true}
                disabled={readOnly}
                onCheckedChange={change}
              />
            ) : (
              <div className="relative min-w-0">
                <Input
                  {...PLAIN_INPUT_PROPS}
                  id={id}
                  required={
                    field.key === 'api_base' &&
                    (customProvider || needsEmbeddingBase)
                      ? true
                      : field.required
                  }
                  readOnly={readOnly}
                  min={field.min}
                  step={field.type === 'number' ? 'any' : undefined}
                  type={
                    field.type === 'number'
                      ? 'number'
                      : field.type === 'secret' && !visible[field.key]
                        ? 'password'
                        : 'text'
                  }
                  className={field.type === 'secret' ? 'h-10 pr-11' : 'h-10'}
                  value={
                    typeof current === 'string' || typeof current === 'number'
                      ? current
                      : ''
                  }
                  onChange={(event) =>
                    change(
                      event.target.value === ''
                        ? null
                        : field.type === 'number'
                          ? Number(event.target.value)
                          : event.target.value,
                    )
                  }
                />
                {field.type === 'secret' && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="absolute top-1 right-1"
                    title={t(
                      visible[field.key] ? 'models.hideKey' : 'models.showKey',
                    )}
                    aria-label={t(
                      visible[field.key] ? 'models.hideKey' : 'models.showKey',
                    )}
                    onClick={() =>
                      setVisible({
                        ...visible,
                        [field.key]: !visible[field.key],
                      })
                    }
                  >
                    {visible[field.key] ? <EyeOffIcon /> : <EyeIcon />}
                  </Button>
                )}
              </div>
            )}
          </Field>
        )
      })}
    </>
  )
}
