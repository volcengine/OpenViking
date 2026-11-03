import { useMutation } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { CircleAlertIcon, KeyRoundIcon, LoaderCircleIcon } from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

import { PLAIN_INPUT_PROPS } from '#/shared/lib/form-input'
import { cn } from '#/shared/lib/utils'
import { Alert, AlertDescription, AlertTitle } from '#/shared/ui/alert'
import { Button } from '#/shared/ui/button'
import { Checkbox } from '#/shared/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from '#/shared/ui/field'
import { Input } from '#/shared/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/shared/ui/select'

import { DocsLink } from '#/features/admin/gateway/components/docs-link'
import { SettingField } from '#/features/admin/gateway/components/setting-field'
import {
  ProtocolBadge,
  ToneBadge,
} from '#/features/admin/gateway/components/status-badges'
import { TagInput } from '#/features/admin/gateway/components/tag-input'
import type {
  IssuedKey,
  KeyOwner,
  KeyRequest,
  KeyUser,
  Profile,
  Upstream,
} from '#/features/admin/gateway/lib/api'
import { issueKey, toGatewayError } from '#/features/admin/gateway/lib/api'
import type { Translate } from '#/features/admin/gateway/lib/localize'
import { gatewayErrorMessage } from '#/features/admin/gateway/lib/localize'
import { requestManagementUnlock } from '#/features/admin/gateway/lib/management-key'
import { NEW_ID } from '#/features/admin/gateway/lib/search'
import { servedModels } from '#/features/admin/gateway/lib/upstream-schema'
import {
  useGateway,
  useKeyUsers,
} from '#/features/admin/gateway/lib/use-gateway'

/** Prefix of gateway key secrets; such a key can't be bound to another key. */
const GATEWAY_KEY_PREFIX = 'ovgw_'
const MODEL_PREVIEW = 3

type KeyField =
  | 'name'
  | 'user_id'
  | 'openviking_key'
  | 'policy_id'
  | 'upstream_ids'

/** Field → i18n key of the first problem; empty when the request can be sent. */
export type KeyRequestErrors = Partial<Record<KeyField, string>>

/** Checks a key request before sending it. */
export function validateKeyRequest(request: KeyRequest): KeyRequestErrors {
  const errors: KeyRequestErrors = {}
  if (!request.name.trim()) errors.name = 'validation.required'
  if (request.user_id !== undefined) {
    if (!request.user_id) errors.user_id = 'validation.required'
  }
  if (request.openviking_key !== undefined) {
    const secret = request.openviking_key.trim()
    if (!secret) errors.openviking_key = 'validation.required'
    else if (secret.startsWith(GATEWAY_KEY_PREFIX)) {
      errors.openviking_key = 'keys.form.openvikingKey.gatewayKey'
    } else if (secret.startsWith('ovgm_')) {
      errors.openviking_key = 'keys.form.openvikingKey.managementKey'
    }
  }
  if (!request.policy_id) errors.policy_id = 'validation.required'
  if (!request.upstream_ids.length) {
    errors.upstream_ids = 'keys.form.upstreams.required'
  }
  return errors
}

/** Stable backend reasons; server wording is deliberately not used for matching. */
const ISSUE_REASONS: Partial<Record<string, [string, boolean]>> = {
  unknown_user: ['keys.errors.unknownUser', true],
  user_required: ['keys.errors.userRequired', true],
  user_key_unreadable: ['keys.errors.userKeyUnreadable', true],
  root_key_not_allowed: ['keys.errors.rootKey', true],
  other_account: ['keys.errors.otherAccount', true],
  openviking_http_401: ['keys.errors.invalidKey', true],
  openviking_http_403: ['keys.errors.invalidKey', true],
  openviking_identity_missing: ['enums.openviking.identityMissing', true],
  openviking_unavailable: ['keys.errors.unavailable', false],
  openviking_unreachable: ['keys.errors.unavailable', false],
  openviking_version_mismatch: ['keys.errors.versionMismatch', false],
}

export type IssueProblem = {
  message: string
  /** Shown under the OpenViking user or key field instead of in the dialog alert. */
  keyField: boolean
}

/** A localized explanation of why issuing a key failed. */
export function issueProblem(t: Translate, error: unknown): IssueProblem {
  const { reason, requiredVersion } = toGatewayError(error)
  const known = ISSUE_REASONS[reason]
  if (known)
    return {
      message:
        t(known[0]) +
        (requiredVersion
          ? ` ${t('keys.errors.requiredVersion', { version: requiredVersion })}`
          : ''),
      keyField: known[1],
    }
  return { message: gatewayErrorMessage(t, error), keyField: false }
}

function byName<T extends { name: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => a.name.localeCompare(b.name))
}

/** Whether the admin picks the key's user or pastes that user's OpenViking key. */
type OwnerMode = 'self' | 'user' | 'key'

/** Form state; keeps the picked user and the pasted key while switching modes. */
type KeyDraft = Omit<KeyRequest, keyof KeyOwner> & {
  user_id: string
  openviking_key: string
}

type KeysIssueDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  profiles: Profile[]
  upstreams: Upstream[]
  /** Receives the new key, whose secret is only available now. */
  onIssued: (issued: IssuedKey) => void
  /** Initial name, profile, upstreams and models instead of an empty form. */
  defaults?: Partial<Omit<KeyRequest, keyof KeyOwner>>
}

/**
 * Form for a new gateway key: name, OpenViking user (or that user's pasted
 * OpenViking key), context profile, upstreams and an optional model
 * allowlist. Mount it with a fresh `key` each time it opens to start from an
 * empty form.
 */
export function KeysIssueDialog({
  open,
  onOpenChange,
  profiles,
  upstreams,
  onIssued,
  defaults,
}: KeysIssueDialogProps) {
  const { t } = useTranslation('gateway')
  const {
    connection,
    invalidate,
    access,
    devMode,
    readOnly,
    canIssueForOthers: canPickUser,
  } = useGateway()
  const selfId = access?.user_id ?? connection.userId
  const usersQuery = useKeyUsers({ enabled: open && canPickUser })
  const formId = React.useId()
  const userTriggerRef = React.useRef<HTMLButtonElement>(null)
  const keyInputRef = React.useRef<HTMLInputElement>(null)
  const [draft, setDraft] = React.useState<KeyDraft>(() => ({
    name: '',
    user_id: '',
    openviking_key: '',
    policy_id: profiles.length === 1 ? profiles[0].id : '',
    upstream_ids: upstreams.length === 1 ? [upstreams[0].id] : [],
    models: [],
    ...defaults,
  }))
  /** 'key' once the admin switches to pasting, or the server asks for it. */
  const [chosenMode, setChosenMode] = React.useState<'user' | 'key'>('user')
  const [submitted, setSubmitted] = React.useState(false)

  const issue = useMutation({
    mutationFn: (request: KeyRequest) => issueKey(connection, request),
    onSuccess: async (issued) => {
      onIssued(issued)
      await invalidate('keys', 'overview', 'tools')
    },
    onError: (error) => {
      // A user, profile or upstream changed meanwhile; show the current lists.
      const { status, reason } = toGatewayError(error)
      if (reason === 'user_key_unreadable') setChosenMode('key')
      if (status === 400 || status === 409) {
        void invalidate('users', 'profiles', 'upstreams')
      }
    },
  })
  const pending = issue.isPending

  const users = [...(usersQuery.data ?? [])].sort((a, b) =>
    a.user_id.localeCompare(b.user_id),
  )
  const fallback =
    canPickUser && usersQuery.isError
      ? 'loadFailed'
      : canPickUser && usersQuery.isSuccess && !users.length
        ? 'none'
        : undefined
  const mode: OwnerMode =
    fallback || chosenMode === 'key' ? 'key' : canPickUser ? 'user' : 'self'
  // Prefer the current identity, and preserve an explicit selection across retries.
  const userId = draft.user_id
    ? users.some((user) => user.user_id === draft.user_id)
      ? draft.user_id
      : ''
    : users.some((user) => user.user_id === selfId)
      ? selfId
      : users.length === 1
        ? users[0].user_id
        : ''
  const selectedUser = users.find((user) => user.user_id === userId)
  // The server can't read this user's key; the caller's own (non-root) key
  // is used as is, so issuing for yourself never needs one.
  const pasteForUser =
    mode === 'user' &&
    selectedUser?.api_key_available === false &&
    (userId !== selfId || access?.role === 'root')

  const owner: KeyOwner =
    mode === 'self'
      ? {}
      : mode === 'user' && !pasteForUser
        ? { user_id: userId }
        : {
            openviking_key: draft.openviking_key.trim(),
            ...(userId ? { user_id: userId } : {}),
          }
  const request: KeyRequest = {
    ...owner,
    name: draft.name.trim(),
    policy_id: draft.policy_id,
    upstream_ids: upstreams
      .map((upstream) => upstream.id)
      .filter((id) => draft.upstream_ids.includes(id)),
    models: draft.models,
  }

  const problem = issue.isError ? issueProblem(t, issue.error) : undefined
  const errors = submitted ? validateKeyRequest(request) : {}
  const ownerError = errors.user_id ?? errors.openviking_key
  // The server's reason first: it can empty the field (say, a removed user).
  const keyError = problem?.keyField
    ? problem.message
    : ownerError
      ? t(ownerError)
      : undefined

  const sortedProfiles = byName(profiles)
  const sortedUpstreams = byName(upstreams)
  const selectedProfile = profiles.find((p) => p.id === draft.policy_id)
  const modelSuggestions = [
    ...new Set(
      upstreams
        .filter((upstream) => draft.upstream_ids.includes(upstream.id))
        .flatMap(servedModels),
    ),
  ].sort()

  function update(patch: Partial<KeyDraft>) {
    setDraft((current) => ({ ...current, ...patch }))
    if (issue.isError) issue.reset()
  }

  // A field that appears (a mode switch, a user whose key must be pasted)
  // takes focus; the switch button goes away with the previous field.
  const focusField = mode === 'key' || pasteForUser ? 'key' : mode
  const focusedField = React.useRef(focusField)
  React.useEffect(() => {
    if (focusedField.current === focusField) return
    focusedField.current = focusField
    const field = focusField === 'user' ? userTriggerRef : keyInputRef
    field.current?.focus()
  }, [focusField])

  function switchMode(next: 'user' | 'key') {
    setChosenMode(next)
    if (issue.isError) issue.reset()
  }

  function toggleUpstream(id: string, checked: boolean) {
    update({
      upstream_ids: checked
        ? [...draft.upstream_ids, id]
        : draft.upstream_ids.filter((other) => other !== id),
    })
  }

  function submit(event: React.FormEvent) {
    event.preventDefault()
    setSubmitted(true)
    if (Object.keys(validateKeyRequest(request)).length) return
    // A preselected user becomes the choice, so a retry goes to the same user.
    if (mode === 'user') {
      setDraft((current) => ({ ...current, user_id: userId }))
    }
    issue.mutate(request)
  }

  const id = (field: string) => `${formId}-${field}`

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!pending) onOpenChange(next)
      }}
    >
      <DialogContent showCloseButton={!pending} className="gap-5 sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="text-lg">{t('keys.form.title')}</DialogTitle>
          <DialogDescription>{t('keys.form.description')}</DialogDescription>
        </DialogHeader>

        <form
          id={id('form')}
          className="grid min-w-0 gap-5"
          noValidate
          onSubmit={submit}
        >
          <SettingField
            label={t('keys.form.name.label')}
            htmlFor={id('name')}
            description={t('keys.form.name.description')}
            error={errors.name ? t(errors.name) : undefined}
          >
            <Input
              id={id('name')}
              value={draft.name}
              placeholder={t('keys.form.name.placeholder')}
              aria-invalid={Boolean(errors.name)}
              disabled={pending}
              autoComplete="off"
              onChange={(event) => update({ name: event.target.value })}
            />
          </SettingField>

          {mode === 'self' ? (
            <SettingField label={t('keys.form.user.label')}>
              <p className="text-sm text-muted-foreground">
                {t(
                  devMode
                    ? 'keys.form.user.developmentIdentity'
                    : 'keys.form.user.self',
                  { user: selfId },
                )}
              </p>
            </SettingField>
          ) : mode === 'user' ? (
            <SettingField
              label={t('keys.form.user.label')}
              htmlFor={id('user')}
              description={
                <>
                  {t('keys.form.user.description')}{' '}
                  <ModeSwitch
                    disabled={pending}
                    onClick={() => switchMode('key')}
                  >
                    {t('keys.form.openvikingKey.paste')}
                  </ModeSwitch>
                </>
              }
              error={pasteForUser ? undefined : keyError}
            >
              <Select
                value={userId || null}
                disabled={pending || !usersQuery.isSuccess}
                onValueChange={(value) => {
                  if (value) update({ user_id: value })
                }}
              >
                <SelectTrigger
                  ref={userTriggerRef}
                  id={id('user')}
                  className="w-full"
                  aria-invalid={!pasteForUser && Boolean(keyError)}
                >
                  <SelectValue>
                    {selectedUser ? (
                      <KeyUserLabel user={selectedUser} />
                    ) : (
                      <span className="text-muted-foreground">
                        {usersQuery.isSuccess
                          ? t('keys.form.user.placeholder')
                          : t('keys.form.user.loading')}
                      </span>
                    )}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent className="max-h-[min(18rem,var(--available-height))]">
                  {users.map((user) => (
                    <SelectItem key={user.user_id} value={user.user_id}>
                      <KeyUserLabel user={user} />
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </SettingField>
          ) : null}
          {mode === 'key' || pasteForUser ? (
            <SettingField
              label={t(
                userId
                  ? 'keys.form.openvikingKey.forUser'
                  : 'keys.form.openvikingKey.label',
                { user: userId },
              )}
              htmlFor={id('openviking-key')}
              description={
                fallback ? (
                  <>
                    <span className="block text-foreground">
                      {t(`keys.form.user.${fallback}`)}
                    </span>
                    {t('keys.form.openvikingKey.description')}
                  </>
                ) : (
                  <>
                    {t('keys.form.openvikingKey.description')}{' '}
                    {canPickUser && mode === 'key' ? (
                      <ModeSwitch
                        disabled={pending}
                        onClick={() => switchMode('user')}
                      >
                        {t('keys.form.user.choose')}
                      </ModeSwitch>
                    ) : null}
                  </>
                )
              }
              error={keyError}
            >
              <Input
                {...PLAIN_INPUT_PROPS}
                ref={keyInputRef}
                id={id('openviking-key')}
                type="password"
                autoComplete="new-password"
                className="font-mono"
                value={draft.openviking_key}
                aria-invalid={Boolean(keyError)}
                disabled={pending}
                onChange={(event) =>
                  update({ openviking_key: event.target.value })
                }
              />
            </SettingField>
          ) : null}

          <SettingField
            label={t('keys.form.profile.label')}
            htmlFor={id('profile')}
            description={
              profiles.length ? (
                t('keys.form.profile.description')
              ) : (
                <>
                  {t(
                    readOnly
                      ? 'keys.prerequisites.managed'
                      : 'keys.form.profile.empty',
                  )}{' '}
                  {readOnly ? (
                    <ModeSwitch
                      disabled={pending}
                      onClick={requestManagementUnlock}
                    >
                      {t('keys.prerequisites.unlock')}
                    </ModeSwitch>
                  ) : (
                    <Link
                      to="/admin/gateway/profiles/$profileId"
                      params={{ profileId: NEW_ID }}
                      className="font-medium text-foreground underline underline-offset-4"
                    >
                      {t('keys.form.profile.create')}
                    </Link>
                  )}
                </>
              )
            }
            error={errors.policy_id ? t(errors.policy_id) : undefined}
          >
            <Select
              value={draft.policy_id || null}
              disabled={pending || !profiles.length}
              onValueChange={(value) => {
                if (value) update({ policy_id: value })
              }}
            >
              <SelectTrigger
                id={id('profile')}
                className="w-full"
                aria-invalid={Boolean(errors.policy_id)}
              >
                <SelectValue>
                  {selectedProfile ? (
                    selectedProfile.name
                  ) : (
                    <span className="text-muted-foreground">
                      {t('keys.form.profile.placeholder')}
                    </span>
                  )}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {sortedProfiles.map((profile) => (
                  <SelectItem key={profile.id} value={profile.id}>
                    {profile.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingField>

          <Field data-invalid={Boolean(errors.upstream_ids)} className="gap-2">
            <FieldLabel id={id('upstreams')}>
              {t('keys.form.upstreams.label')}
            </FieldLabel>
            {upstreams.length ? (
              <div
                role="group"
                aria-labelledby={id('upstreams')}
                className="grid gap-2"
              >
                {sortedUpstreams.map((upstream) => (
                  <UpstreamOption
                    key={upstream.id}
                    upstream={upstream}
                    checked={draft.upstream_ids.includes(upstream.id)}
                    disabled={pending}
                    onCheckedChange={(checked) =>
                      toggleUpstream(upstream.id, checked)
                    }
                  />
                ))}
              </div>
            ) : null}
            <FieldDescription className="text-xs">
              {upstreams.length ? (
                t('keys.form.upstreams.description')
              ) : (
                <>
                  {t(
                    readOnly
                      ? 'keys.prerequisites.managed'
                      : 'keys.form.upstreams.empty',
                  )}{' '}
                  {readOnly ? (
                    <ModeSwitch
                      disabled={pending}
                      onClick={requestManagementUnlock}
                    >
                      {t('keys.prerequisites.unlock')}
                    </ModeSwitch>
                  ) : (
                    <Link
                      to="/admin/gateway/upstreams/$upstreamId"
                      params={{ upstreamId: NEW_ID }}
                      className="font-medium text-foreground underline underline-offset-4"
                    >
                      {t('keys.form.upstreams.create')}
                    </Link>
                  )}
                </>
              )}
            </FieldDescription>
            {errors.upstream_ids ? (
              <FieldError>{t(errors.upstream_ids)}</FieldError>
            ) : null}
          </Field>

          <SettingField
            label={
              <>
                {t('keys.form.models.label')}
                <span className="font-normal text-muted-foreground">
                  {t('keys.form.models.optional')}
                </span>
              </>
            }
            htmlFor={id('models')}
            description={t('keys.form.models.description')}
          >
            <TagInput
              id={id('models')}
              value={draft.models}
              suggestions={modelSuggestions}
              placeholder={t('keys.form.models.placeholder')}
              disabled={pending}
              onChange={(models) => update({ models })}
            />
          </SettingField>

          {problem && !problem.keyField ? (
            <Alert variant="destructive">
              <CircleAlertIcon />
              <AlertTitle>{t('keys.errors.title')}</AlertTitle>
              <AlertDescription>
                {problem.message}{' '}
                <DocsLink section="issueKeyFails" variant="text">
                  {t('docs.troubleshoot')}
                </DocsLink>
              </AlertDescription>
            </Alert>
          ) : null}
        </form>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={() => onOpenChange(false)}
          >
            {t('actions.cancel')}
          </Button>
          <Button type="submit" form={id('form')} disabled={pending}>
            {pending ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : (
              <KeyRoundIcon />
            )}
            {t('keys.form.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Text button at the end of a field description that switches how the user is given. */
function ModeSwitch({
  disabled,
  onClick,
  children,
}: {
  disabled: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      className="font-medium text-foreground underline underline-offset-4 disabled:opacity-50"
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  )
}

/** A user's ID with a quiet role tag and, when they can't be picked, why. */
function KeyUserLabel({ user }: { user: KeyUser }) {
  const { t } = useTranslation('gateway')
  return (
    <span className="flex min-w-0 items-baseline gap-2">
      <span className="truncate">{user.user_id}</span>
      <span className="text-xs text-muted-foreground">
        {t(`keys.form.user.roles.${user.role}`)}
      </span>
      {/* Older OpenViking servers omit the field; only `false` is known. */}
      {user.api_key_available === false ? (
        <span className="text-xs text-muted-foreground">
          {t('keys.form.user.unavailable')}
        </span>
      ) : null}
    </span>
  )
}

/** One selectable upstream: name, protocol, the models it serves and its state. */
function UpstreamOption({
  upstream,
  checked,
  disabled,
  onCheckedChange,
}: {
  upstream: Upstream
  checked: boolean
  disabled: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  const { t } = useTranslation('gateway')
  const models = servedModels(upstream)
  const shown = models.slice(0, MODEL_PREVIEW)
  return (
    <label
      className={cn(
        'flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors hover:bg-muted/30',
        checked && 'border-primary/30 bg-primary/[0.03]',
        disabled && 'pointer-events-none opacity-60',
      )}
    >
      <Checkbox
        className="mt-0.5"
        checked={checked}
        disabled={disabled}
        onCheckedChange={(next) => onCheckedChange(next)}
      />
      <span className="grid min-w-0 flex-1 gap-1">
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="truncate text-sm font-medium">{upstream.name}</span>
          <ProtocolBadge protocol={upstream.protocol} />
          {upstream.coding_plan && !upstream.allow_coding_plan ? (
            <ToneBadge tone="neutral">
              {t('keys.form.upstreams.blocked')}
            </ToneBadge>
          ) : null}
          {upstream.enabled ? null : (
            <ToneBadge tone="neutral">{t('keys.form.upstreams.off')}</ToneBadge>
          )}
        </span>
        <span
          className={cn(
            'truncate text-xs text-muted-foreground',
            models.length > 0 && 'font-mono',
          )}
          title={models.join(', ') || undefined}
        >
          {models.length
            ? [
                shown.join(', '),
                models.length > shown.length
                  ? t('keys.extra', { count: models.length - shown.length })
                  : '',
              ]
                .filter(Boolean)
                .join(' ')
            : t('upstreams.models.any')}
        </span>
      </span>
    </label>
  )
}
