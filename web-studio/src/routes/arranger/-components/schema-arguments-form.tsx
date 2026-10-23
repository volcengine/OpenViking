import { useEffect, useMemo, useState } from 'react'
import Form from '@rjsf/core'
import { getInputProps, toErrorSchema } from '@rjsf/utils'
import type {
  ArrayFieldItemTemplateProps,
  ArrayFieldTemplateProps,
  BaseInputTemplateProps,
  FieldTemplateProps,
  ObjectFieldTemplateProps,
  RJSFSchema,
  RJSFValidationError,
  UiSchema,
  WidgetProps,
} from '@rjsf/utils'
import validator from '@rjsf/validator-ajv8'
import { PlusIcon, Trash2Icon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { Switch } from '#/components/ui/switch'
import { Textarea } from '#/components/ui/textarea'
import { cn } from '#/lib/utils'

import type { JsonSchema } from '../-lib/model'
import { cleanSchemaArguments, toRjsfSchema } from '../-lib/schema'
import type { SchemaFormObject } from '../-lib/schema'

type SchemaArgumentsFormProps = {
  errors: RJSFValidationError[]
  onChange: (value: SchemaFormObject) => void
  schema: JsonSchema
  values: SchemaFormObject
}

function schemaTypeLabel(schema: RJSFSchema): string {
  if (typeof schema.type === 'string') return schema.type
  if (Array.isArray(schema.type)) return schema.type.join(' | ')
  if (schema.oneOf) return 'oneOf'
  if (schema.anyOf) return 'anyOf'
  if (schema.properties) return 'object'
  if (schema.items) return 'array'
  return 'value'
}

function SchemaHeading({
  htmlFor,
  label,
  required,
  type,
}: {
  htmlFor?: string
  label: string
  required?: boolean
  type: string
}) {
  const { t } = useTranslation('arranger')
  return (
    <div className="flex min-w-0 items-center gap-2">
      <Label
        htmlFor={htmlFor}
        className="min-w-0 break-words text-sm font-medium"
      >
        {label}
      </Label>
      <Badge
        variant={required ? 'default' : 'outline'}
        className="shrink-0 px-1.5 py-0 text-[10px]"
      >
        {required ? t('fields.required') : t('fields.optional')}
      </Badge>
      <span className="ml-auto shrink-0 font-mono text-[10px] text-muted-foreground">
        {type}
      </span>
    </div>
  )
}

function StudioFieldTemplate({
  children,
  description,
  displayLabel,
  hidden,
  id,
  label,
  rawErrors,
  required,
  schema,
}: FieldTemplateProps) {
  const { t } = useTranslation('arranger')
  if (hidden) return <div className="hidden">{children}</div>

  const type = schemaTypeLabel(schema)
  const nested = type === 'object'
  return (
    <div className={cn('min-w-0 space-y-1.5', nested && 'space-y-3')}>
      {displayLabel && (
        <SchemaHeading
          htmlFor={id}
          label={label}
          required={required}
          type={type}
        />
      )}
      {displayLabel && description}
      <div
        className={cn(
          nested && displayLabel && 'border-l-2 border-border pl-4',
        )}
      >
        {children}
      </div>
      {rawErrors?.map((error, index) => (
        <p key={`${error}-${index}`} className="text-xs text-destructive">
          {error.includes('must have required property')
            ? t('validation.required')
            : error}
        </p>
      ))}
    </div>
  )
}

function StudioObjectFieldTemplate({
  description,
  fieldPathId,
  properties,
  required,
  title,
}: ObjectFieldTemplateProps) {
  const showHeading =
    fieldPathId.path.length > 0 &&
    typeof fieldPathId.path[fieldPathId.path.length - 1] !== 'number'
  return (
    <div
      id={fieldPathId.$id}
      className={cn('space-y-4', fieldPathId.path.length === 0 && 'space-y-5')}
    >
      {showHeading && (
        <div className="space-y-1">
          <SchemaHeading label={title} required={required} type="object" />
          {description && (
            <p className="whitespace-pre-line text-xs leading-5 text-muted-foreground">
              {description}
            </p>
          )}
        </div>
      )}
      <div
        className={cn(
          'space-y-4',
          showHeading && 'border-l-2 border-border pl-4',
        )}
      >
        {properties.map((property) => (
          <div key={property.name}>{property.content}</div>
        ))}
      </div>
    </div>
  )
}

function StudioArrayFieldTemplate({
  canAdd,
  disabled,
  fieldPathId,
  items,
  onAddClick,
  readonly,
  required,
  schema,
  title,
}: ArrayFieldTemplateProps) {
  const { t } = useTranslation('arranger')
  return (
    <div id={fieldPathId.$id} className="space-y-3">
      {fieldPathId.path.length > 0 && (
        <div className="space-y-1">
          <SchemaHeading label={title} required={required} type="array" />
          {schema.description && (
            <p className="whitespace-pre-line text-xs leading-5 text-muted-foreground">
              {schema.description}
            </p>
          )}
        </div>
      )}
      <div
        className={cn(
          'space-y-3',
          fieldPathId.path.length > 0 && 'border-l-2 border-border pl-4',
        )}
      >
        {items}
        {canAdd && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || readonly}
            onClick={onAddClick}
          >
            <PlusIcon data-icon="inline-start" />
            {t('mount.addItem')}
          </Button>
        )}
      </div>
    </div>
  )
}

function StudioArrayFieldItemTemplate({
  buttonsProps,
  children,
  hasToolbar,
  index,
  itemKey,
}: ArrayFieldItemTemplateProps) {
  const { t } = useTranslation('arranger')
  return (
    <div
      key={itemKey}
      className="min-w-0 space-y-3 rounded-md border bg-muted/10 p-3"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium text-muted-foreground">
          {t('mount.itemLabel', { index: index + 1 })}
        </span>
        {hasToolbar && buttonsProps.hasRemove && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            title={t('mount.removeItem')}
            aria-label={t('mount.removeItem')}
            disabled={buttonsProps.disabled || buttonsProps.readonly}
            onClick={buttonsProps.onRemoveItem}
          >
            <Trash2Icon />
          </Button>
        )}
      </div>
      {children}
    </div>
  )
}

function StudioBaseInputTemplate(props: BaseInputTemplateProps) {
  const {
    autofocus,
    disabled,
    id,
    onBlur,
    onChange,
    onFocus,
    options,
    placeholder,
    rawErrors,
    readonly,
    schema,
    type,
    value,
  } = props
  const inputProps = getInputProps(schema, type, options)
  return (
    <Input
      id={id}
      type={inputProps.type}
      step={inputProps.step}
      min={inputProps.min}
      max={inputProps.max}
      minLength={schema.minLength}
      maxLength={schema.maxLength}
      pattern={schema.pattern}
      value={value ?? ''}
      placeholder={placeholder}
      disabled={disabled}
      readOnly={readonly}
      autoFocus={autofocus}
      aria-invalid={Boolean(rawErrors?.length)}
      onBlur={(event) => onBlur(id, event.target.value)}
      onFocus={(event) => onFocus(id, event.target.value)}
      onChange={(event) =>
        onChange(
          event.target.value === '' ? options.emptyValue : event.target.value,
        )
      }
    />
  )
}

function StudioSelectWidget({
  disabled,
  id,
  onChange,
  options,
  placeholder,
  rawErrors,
  readonly,
  required,
  value,
}: WidgetProps) {
  const { t } = useTranslation('arranger')
  const enumOptions = (options.enumOptions || []) as Array<{
    label: string
    value: unknown
  }>
  const selectedIndex = enumOptions.findIndex(
    (option) => JSON.stringify(option.value) === JSON.stringify(value),
  )
  return (
    <Select
      value={selectedIndex < 0 ? null : String(selectedIndex)}
      disabled={disabled || readonly}
      onValueChange={(next) => {
        if (next === null || next === '__unset__') {
          onChange(options.emptyValue)
          return
        }
        onChange(enumOptions[Number(next)]?.value)
      }}
    >
      <SelectTrigger
        id={id}
        className="w-full"
        aria-invalid={Boolean(rawErrors?.length)}
      >
        <SelectValue>
          {selectedIndex < 0
            ? placeholder || t('mount.selectValue')
            : enumOptions[selectedIndex].label}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {!required && (
          <SelectItem value="__unset__">{t('mount.notSet')}</SelectItem>
        )}
        {enumOptions.map((option, index) => (
          <SelectItem key={`${option.label}-${index}`} value={String(index)}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function StudioCheckboxWidget({
  disabled,
  id,
  onChange,
  readonly,
  required,
  value,
}: WidgetProps) {
  const { t } = useTranslation('arranger')
  if (!required) {
    return (
      <Select
        value={value === undefined ? null : String(value)}
        disabled={disabled || readonly}
        onValueChange={(next) =>
          onChange(
            next === null || next === '__unset__' ? undefined : next === 'true',
          )
        }
      >
        <SelectTrigger id={id} className="w-full">
          <SelectValue>
            {value === undefined
              ? t('mount.selectValue')
              : value
                ? t('values.true')
                : t('values.false')}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__unset__">{t('mount.notSet')}</SelectItem>
          <SelectItem value="true">{t('values.true')}</SelectItem>
          <SelectItem value="false">{t('values.false')}</SelectItem>
        </SelectContent>
      </Select>
    )
  }
  return (
    <Switch
      id={id}
      checked={Boolean(value)}
      disabled={disabled || readonly}
      onCheckedChange={(checked) => onChange(checked)}
    />
  )
}

function StudioTextareaWidget({
  autofocus,
  disabled,
  id,
  onBlur,
  onChange,
  onFocus,
  options,
  placeholder,
  rawErrors,
  readonly,
  value,
}: WidgetProps) {
  return (
    <Textarea
      id={id}
      value={value ?? ''}
      rows={typeof options.rows === 'number' ? options.rows : undefined}
      placeholder={placeholder}
      disabled={disabled}
      readOnly={readonly}
      autoFocus={autofocus}
      aria-invalid={Boolean(rawErrors?.length)}
      onBlur={(event) => onBlur(id, event.target.value)}
      onFocus={(event) => onFocus(id, event.target.value)}
      onChange={(event) =>
        onChange(
          event.target.value === '' ? options.emptyValue : event.target.value,
        )
      }
    />
  )
}

const uiSchema: UiSchema = {
  'ui:submitButtonOptions': { norender: true },
}

const templates = {
  ArrayFieldItemTemplate: StudioArrayFieldItemTemplate,
  ArrayFieldTemplate: StudioArrayFieldTemplate,
  BaseInputTemplate: StudioBaseInputTemplate,
  FieldTemplate: StudioFieldTemplate,
  ObjectFieldTemplate: StudioObjectFieldTemplate,
}

const widgets = {
  CheckboxWidget: StudioCheckboxWidget,
  SelectWidget: StudioSelectWidget,
  TextareaWidget: StudioTextareaWidget,
}

export function SchemaArgumentsForm({
  errors,
  onChange,
  schema,
  values,
}: SchemaArgumentsFormProps) {
  const { t } = useTranslation('arranger')
  const [editAsJson, setEditAsJson] = useState(false)
  const [jsonDraft, setJsonDraft] = useState('')
  const [jsonError, setJsonError] = useState('')
  const rjsfSchema = useMemo(() => toRjsfSchema(schema), [schema])
  const hasFields = Boolean(
    rjsfSchema.properties ||
    rjsfSchema.additionalProperties ||
    rjsfSchema.oneOf ||
    rjsfSchema.anyOf,
  )

  useEffect(() => {
    if (editAsJson) return
    setJsonDraft(JSON.stringify(cleanSchemaArguments(schema, values), null, 2))
  }, [editAsJson, schema, values])

  if (!hasFields) {
    return (
      <div className="rounded-md border border-dashed px-3 py-4 text-sm text-muted-foreground">
        {t('mount.noArguments')}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end gap-2">
        <Label htmlFor="mount-edit-as-json" className="text-xs font-normal">
          {t('mount.editAsJson')}
        </Label>
        <Switch
          id="mount-edit-as-json"
          checked={editAsJson}
          onCheckedChange={(checked) => {
            setEditAsJson(checked)
            setJsonError('')
            if (checked) {
              setJsonDraft(
                JSON.stringify(cleanSchemaArguments(schema, values), null, 2),
              )
            }
          }}
        />
      </div>
      {editAsJson ? (
        <div className="space-y-1.5">
          <Textarea
            value={jsonDraft}
            onChange={(event) => {
              const next = event.target.value
              setJsonDraft(next)
              try {
                const parsed = JSON.parse(next) as unknown
                if (
                  !parsed ||
                  typeof parsed !== 'object' ||
                  Array.isArray(parsed)
                ) {
                  throw new Error(t('mount.expectedJsonObject'))
                }
                onChange(parsed as SchemaFormObject)
                setJsonError('')
              } catch (error) {
                setJsonError(
                  error instanceof Error ? error.message : String(error),
                )
              }
            }}
            className="min-h-72 font-mono text-xs"
            aria-invalid={Boolean(jsonError)}
          />
          {jsonError && <p className="text-xs text-destructive">{jsonError}</p>}
        </div>
      ) : (
        <Form
          schema={rjsfSchema}
          validator={validator}
          formData={values}
          uiSchema={uiSchema}
          templates={templates}
          widgets={widgets}
          extraErrors={toErrorSchema(errors)}
          extraErrorsBlockSubmit
          noHtml5Validate
          omitExtraData
          liveOmit="onChange"
          showErrorList={false}
          experimental_defaultFormStateBehavior={{
            allOf: 'populateDefaults',
            arrayMinItems: { populate: 'requiredOnly' },
            emptyObjectFields: 'skipEmptyDefaults',
            requiredBooleanDefault: 'skip',
          }}
          onChange={({ formData }) =>
            onChange((formData || {}) as SchemaFormObject)
          }
        />
      )}
    </div>
  )
}
