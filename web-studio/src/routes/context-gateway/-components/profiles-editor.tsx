import * as React from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link, getRouteApi, useNavigate } from '@tanstack/react-router'
import {
  ArrowLeftIcon,
  CircleAlertIcon,
  CopyPlusIcon,
  EllipsisIcon,
  RotateCcwIcon,
  SearchXIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import { Card, CardContent } from '#/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '#/components/ui/dropdown-menu'
import { Input } from '#/components/ui/input'
import { cn } from '#/lib/utils'

import { keysUsing, newObjectId, saveProfile } from '../-lib/api'
import type { Profile, ProfileSettings } from '../-lib/api'
import { gatewayErrorMessage } from '../-lib/localize'
import {
  PROFILE_DEFAULTS,
  duplicateProfile,
  toProfileSettings,
  validateProfile,
} from '../-lib/profile-schema'
import { NEW_ID } from '../-lib/search'
import { useGateway, useKeys, useProfiles } from '../-lib/use-gateway'
import { isValid } from '../-lib/validation'
import type { ValidationErrors } from '../-lib/validation'
import { EditorFooter } from './editor-footer'
import { EmptyState, ErrorState, LoadingState } from './empty-state'
import { Notice } from './notice'
import {
  PROFILE_SECTIONS,
  ProfileSettingsForm,
  isSectionOn,
  sectionAnchor,
  sectionsWithErrors,
} from './profiles-settings'
import { BackLink, SectionHeader } from './section-header'
import { SettingField } from './setting-field'

/** Key-order-independent copy, so reordered objects still compare equal. */
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, child]) => [key, canonical(child)]),
    )
  }
  return value
}

function sameSettings(a: ProfileSettings, b: ProfileSettings): boolean {
  return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b))
}

function SectionNav({
  settings,
  errors,
}: {
  settings: ProfileSettings
  errors: ValidationErrors
}) {
  const { t } = useTranslation('contextGateway')
  const invalid = sectionsWithErrors(errors)
  return (
    <nav aria-label={t('profiles.editor.sections')} className="hidden lg:block">
      <ul className="sticky top-4 grid gap-1">
        {PROFILE_SECTIONS.map(({ id, icon: Icon }) => {
          const on = isSectionOn(settings, id)
          return (
            <li key={id}>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                onClick={() =>
                  document
                    .getElementById(sectionAnchor(id))
                    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }
              >
                <Icon className="size-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">
                  {t(`profiles.${id}.title`)}
                </span>
                {invalid.has(id) ? (
                  <CircleAlertIcon className="size-3.5 shrink-0 text-destructive" />
                ) : (
                  <span
                    className={cn(
                      'size-1.5 shrink-0 rounded-full',
                      on ? 'bg-emerald-500' : 'bg-muted-foreground/40',
                    )}
                  />
                )}
                <span className="sr-only">
                  {t(on ? 'states.on' : 'states.off')}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

type ProfileEditorFormProps = {
  /** Route param: an existing id, or `new`. */
  profileId: string
  /** The profile being edited, or the one being duplicated. */
  source?: Profile
  /** A copy was requested but its source no longer exists. */
  sourceMissing: boolean
  /** Keys that use the profile; undefined while unknown or for new profiles. */
  usedBy?: number
}

function ProfileEditorForm({
  profileId,
  source,
  sourceMissing,
  usedBy,
}: ProfileEditorFormProps) {
  const { t } = useTranslation('contextGateway')
  const navigate = useNavigate()
  const { connection, invalidate } = useGateway()
  const isNew = profileId === NEW_ID
  const [targetId] = React.useState(() => (isNew ? newObjectId() : profileId))
  const baseline = React.useMemo(
    () => (!isNew && source ? toProfileSettings(source) : undefined),
    [isNew, source],
  )
  const [draft, setDraft] = React.useState<ProfileSettings>(() => {
    if (!isNew && source) return toProfileSettings(source)
    if (source) {
      return duplicateProfile(
        source,
        t('profiles.editor.copyName', { name: source.name }),
      )
    }
    return { ...PROFILE_DEFAULTS, name: '' }
  })
  const [nameTouched, setNameTouched] = React.useState(false)
  const update = React.useCallback(
    (patch: Partial<ProfileSettings>) =>
      setDraft((current) => ({ ...current, ...patch })),
    [],
  )

  const errors = React.useMemo(() => validateProfile(draft), [draft])
  const valid = isValid(errors)
  const dirty = !baseline || !sameSettings(draft, baseline)
  const showNameError = Boolean(errors.name) && (nameTouched || !isNew)

  const save = useMutation({
    mutationFn: (settings: ProfileSettings) =>
      saveProfile(connection, targetId, settings),
    onSuccess: async (_saved, settings) => {
      toast.success(t('profiles.toast.saved', { name: settings.name }))
      await invalidate('profiles')
      void navigate({ to: '/context-gateway/profiles' })
    },
    onError: (error) => toast.error(gatewayErrorMessage(t, error)),
  })
  const canSave = valid && dirty && !save.isPending

  function submit(event: React.FormEvent) {
    event.preventDefault()
    setNameTouched(true)
    if (!canSave) return
    save.mutate(toProfileSettings({ ...draft, name: draft.name.trim() }))
  }

  function resetToRecommended() {
    setDraft((current) => ({
      ...current,
      ...PROFILE_DEFAULTS,
      name: current.name,
    }))
    toast.success(t('profiles.toast.reset'))
  }

  // Only a missing name on a new profile is a hint; anything else needs fixing.
  const needsNameOnly =
    Object.keys(errors).length === 1 && Boolean(errors.name) && !showNameError
  const status = !valid
    ? t(needsNameOnly ? 'profiles.editor.needsName' : 'profiles.editor.invalid')
    : dirty && !isNew
      ? t('profiles.editor.unsaved')
      : undefined

  const description =
    usedBy === undefined
      ? undefined
      : usedBy > 0
        ? t('profiles.usedBy', { count: usedBy })
        : t('profiles.unused')

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <SectionHeader
        back={
          <BackLink
            to="/context-gateway/profiles"
            label={t('profiles.editor.back')}
          />
        }
        title={isNew || !source ? t('profiles.editor.newTitle') : source.name}
        description={description}
        actions={
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  type="button"
                  variant="outline"
                  size="icon-sm"
                  aria-label={t('actions.more')}
                  title={t('actions.more')}
                />
              }
            >
              <EllipsisIcon />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-auto min-w-52">
              <DropdownMenuItem onClick={resetToRecommended}>
                <RotateCcwIcon />
                {t('profiles.actions.reset')}
              </DropdownMenuItem>
              {isNew ? null : (
                <DropdownMenuItem
                  onClick={() =>
                    void navigate({
                      to: '/context-gateway/profiles/$profileId',
                      params: { profileId: NEW_ID },
                      search: { from: profileId },
                    })
                  }
                >
                  <CopyPlusIcon />
                  {t('actions.duplicate')}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />

      <form noValidate className="flex flex-col gap-5" onSubmit={submit}>
        <Notice tone="info">{t('profiles.editor.banner')}</Notice>
        {sourceMissing ? (
          <Notice tone="warning">{t('profiles.editor.sourceMissing')}</Notice>
        ) : null}

        <div className="grid gap-6 lg:grid-cols-[12rem_minmax(0,1fr)]">
          <SectionNav settings={draft} errors={errors} />
          <div className="grid min-w-0 gap-4">
            <Card className="gap-0 py-0">
              <CardContent className="py-5">
                <SettingField
                  label={t('profiles.name.label')}
                  htmlFor="profile-name"
                  description={t('profiles.name.description')}
                  error={
                    showNameError && errors.name
                      ? t(errors.name.key, errors.name.values)
                      : undefined
                  }
                  className="max-w-md"
                >
                  <Input
                    id="profile-name"
                    value={draft.name}
                    autoComplete="off"
                    placeholder={t('profiles.name.placeholder')}
                    aria-invalid={showNameError}
                    onBlur={() => setNameTouched(true)}
                    onChange={(event) => update({ name: event.target.value })}
                  />
                </SettingField>
              </CardContent>
            </Card>
            <ProfileSettingsForm
              value={draft}
              onChange={update}
              errors={errors}
            />
          </div>
        </div>

        <EditorFooter
          status={status}
          invalid={!valid && !needsNameOnly}
          cancelTo="/context-gateway/profiles"
          saveLabel={t(isNew ? 'profiles.editor.create' : 'actions.save')}
          saving={save.isPending}
          disabled={!canSave}
        />
      </form>
    </div>
  )
}

type ProfileEditorProps = {
  /** Route param: an existing profile id, or `new` to create one. */
  profileId: string
  /** With `new`: the id of the profile to duplicate. */
  from?: string
}

/**
 * Profile editor page body: loads the profile (or the one to duplicate) and
 * renders the form, or a loading, error or not-found state.
 */
export function ProfileEditor({ profileId, from }: ProfileEditorProps) {
  const { t } = useTranslation('contextGateway')
  const isNew = profileId === NEW_ID
  const sourceId = isNew ? from : profileId
  const profiles = useProfiles({ enabled: Boolean(sourceId) })
  const keys = useKeys({ enabled: !isNew })

  const backLink = (
    <BackLink
      to="/context-gateway/profiles"
      label={t('profiles.editor.back')}
    />
  )

  if (sourceId && !profiles.data) {
    return (
      <div className="flex w-full min-w-0 flex-col gap-5">
        {backLink}
        <Card className="py-0">
          {profiles.isError ? (
            <ErrorState
              title={t('profiles.loadFailed')}
              error={profiles.error}
              retrying={profiles.isFetching}
              onRetry={() => void profiles.refetch()}
            />
          ) : (
            <LoadingState />
          )}
        </Card>
      </div>
    )
  }

  const source = sourceId
    ? profiles.data?.find((profile) => profile.id === sourceId)
    : undefined

  if (!isNew && !source) {
    return (
      <div className="flex w-full min-w-0 flex-col gap-5">
        {backLink}
        <Card className="py-0">
          <EmptyState
            icon={<SearchXIcon />}
            title={t('profiles.editor.notFound.title')}
            description={t('profiles.editor.notFound.description')}
            action={
              <Button
                variant="outline"
                size="sm"
                nativeButton={false}
                render={<Link to="/context-gateway/profiles" />}
              >
                <ArrowLeftIcon />
                {t('profiles.editor.back')}
              </Button>
            }
          />
        </Card>
      </div>
    )
  }

  return (
    <ProfileEditorForm
      key={`${profileId}:${from ?? ''}`}
      profileId={profileId}
      source={source}
      sourceMissing={isNew && Boolean(from) && !source}
      usedBy={isNew ? undefined : keysUsing(keys.data, { profileId })}
    />
  )
}

const route = getRouteApi('/context-gateway/profiles/$profileId')

/** Create (`$profileId` = `new`, `?from=<id>` duplicates) or edit a context profile. */
export function ProfileEditorPage() {
  const { profileId } = route.useParams()
  const { from } = route.useSearch()
  return <ProfileEditor profileId={profileId} from={from} />
}
