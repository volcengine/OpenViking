import * as React from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link, getRouteApi, useNavigate } from '@tanstack/react-router'
import { ArrowLeftIcon, SearchXIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import { Card } from '#/components/ui/card'

import { keysUsing, newObjectId, saveUpstream } from '../-lib/api'
import type { Upstream, UpstreamInput } from '../-lib/api'
import { gatewayErrorMessage } from '../-lib/localize'
import { NEW_ID } from '../-lib/search'
import {
  UPSTREAM_DEFAULTS,
  toUpstreamInput,
  validateUpstream,
} from '../-lib/upstream-schema'
import { useGateway, useKeys, useUpstreams } from '../-lib/use-gateway'
import { isValid } from '../-lib/validation'
import { EditorFooter } from './editor-footer'
import { EmptyState, ErrorState, LoadingState } from './empty-state'
import { BackLink, SectionHeader } from './section-header'
import {
  DeleteUpstreamButton,
  DeleteUpstreamDialog,
  UpstreamTest,
} from './upstreams-actions'
import { UPSTREAM_FIELD_LABELS, UpstreamForm } from './upstreams-form'
import type { UpstreamField } from './upstreams-form'

const route = getRouteApi('/context-gateway/upstreams/$upstreamId')

/** Create (`$upstreamId` = `new`) or edit an upstream. */
export function UpstreamEditorPage() {
  const { upstreamId } = route.useParams()
  return <UpstreamEditor key={upstreamId} upstreamId={upstreamId} />
}

/** Loads the upstream (unless creating one) and shows the editor or a state. */
export function UpstreamEditor({ upstreamId }: { upstreamId: string }) {
  const { t } = useTranslation('contextGateway')
  const isNew = upstreamId === NEW_ID
  const upstreams = useUpstreams({ enabled: !isNew })
  const keys = useKeys({ enabled: !isNew })

  if (isNew) return <UpstreamEditorForm />

  const stored = upstreams.data?.find((item) => item.id === upstreamId)
  if (stored) {
    return (
      <UpstreamEditorForm
        stored={stored}
        usedBy={keysUsing(keys.data, { upstreamId: stored.id })}
      />
    )
  }

  let state: React.ReactNode
  if (upstreams.isPending) {
    state = <LoadingState />
  } else if (upstreams.isError) {
    state = (
      <ErrorState
        title={t('upstreams.loadFailed')}
        error={upstreams.error}
        retrying={upstreams.isFetching}
        onRetry={() => void upstreams.refetch()}
      />
    )
  } else {
    state = (
      <EmptyState
        icon={<SearchXIcon />}
        title={t('upstreams.editor.notFound.title')}
        description={t('upstreams.editor.notFound.description')}
        action={
          <Button
            render={<Link to="/context-gateway/upstreams" />}
            nativeButton={false}
            variant="outline"
            size="sm"
          >
            <ArrowLeftIcon />
            {t('upstreams.editor.back')}
          </Button>
        }
      />
    )
  }
  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <BackLink
        to="/context-gateway/upstreams"
        label={t('upstreams.editor.back')}
      />
      <Card className="py-0">{state}</Card>
    </div>
  )
}

/** Trims text a user may paste with stray spaces before saving. */
function cleanInput(input: UpstreamInput): UpstreamInput {
  return {
    ...input,
    name: input.name.trim(),
    base_url: input.base_url.trim(),
    api_key: input.api_key.trim(),
  }
}

type UpstreamEditorFormProps = {
  /** The saved upstream; absent when creating one. */
  stored?: Upstream
  /** Keys that use the saved upstream, once known. */
  usedBy?: number
}

function UpstreamEditorForm({ stored, usedBy }: UpstreamEditorFormProps) {
  const { t } = useTranslation('contextGateway')
  const navigate = useNavigate()
  const { connection, invalidate } = useGateway()
  const [id] = React.useState(() => stored?.id ?? newObjectId())
  const [initial] = React.useState(() =>
    stored ? toUpstreamInput(stored) : UPSTREAM_DEFAULTS,
  )
  const [draft, setDraft] = React.useState(initial)
  const [visited, setVisited] = React.useState<ReadonlySet<UpstreamField>>(
    () => new Set(),
  )
  const [deleting, setDeleting] = React.useState<Upstream | null>(null)

  const errors = React.useMemo(
    () => validateUpstream(draft, stored),
    [draft, stored],
  )
  const valid = isValid(errors)
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial)

  const visit = React.useCallback((field: UpstreamField) => {
    setVisited((current) =>
      current.has(field) ? current : new Set(current).add(field),
    )
  }, [])

  /** Sets a field without visiting it, for values the form fills in itself. */
  const fill = React.useCallback(
    <TField extends UpstreamField>(
      field: TField,
      value: UpstreamInput[TField],
    ) => {
      setDraft((current) => ({ ...current, [field]: value }))
    },
    [],
  )

  const change = React.useCallback(
    <TField extends UpstreamField>(
      field: TField,
      value: UpstreamInput[TField],
    ) => {
      fill(field, value)
      visit(field)
    },
    [fill, visit],
  )

  const errorFor = (field: UpstreamField) => {
    const issue = errors[field]
    // The editor never picks a protocol the provider lacks, so that error
    // comes from a stored upstream and shows before any field is visited.
    const shown = visited.has(field) || field === 'protocol'
    return issue && shown ? t(issue.key, issue.values) : undefined
  }

  const save = useMutation({
    mutationFn: () => saveUpstream(connection, id, cleanInput(draft)),
    onSuccess: async (saved) => {
      toast.success(
        t(stored ? 'upstreams.toast.saved' : 'upstreams.toast.created', {
          name: saved.name,
        }),
      )
      await invalidate('upstreams')
      await navigate({ to: '/context-gateway/upstreams' })
    },
    onError: (error) => toast.error(gatewayErrorMessage(t, error)),
  })

  const canSave = dirty && valid && !save.isPending
  const missing = Object.keys(errors)
    .map((field) => UPSTREAM_FIELD_LABELS[field as UpstreamField])
    .filter((label): label is string => Boolean(label))
    .map((label) => t(`upstreams.form.${label}.label`))
    .join(t('upstreams.editor.separator'))

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (canSave) save.mutate()
  }

  return (
    <>
      <form
        noValidate
        className="flex w-full min-w-0 flex-col gap-5"
        onSubmit={submit}
      >
        <SectionHeader
          back={
            <BackLink
              to="/context-gateway/upstreams"
              label={t('upstreams.editor.back')}
            />
          }
          title={stored ? stored.name : t('upstreams.editor.newTitle')}
          description={t(
            stored
              ? 'upstreams.editor.editDescription'
              : 'upstreams.editor.newDescription',
          )}
          actions={
            stored ? (
              <>
                <UpstreamTest
                  upstream={stored}
                  variant="header"
                  note={dirty ? t('upstreams.test.savedOnly') : undefined}
                />
                <DeleteUpstreamButton
                  usedBy={usedBy}
                  onClick={() => setDeleting(stored)}
                />
              </>
            ) : null
          }
        />

        <UpstreamForm
          draft={draft}
          stored={stored}
          onChange={change}
          onFill={fill}
          onBlur={visit}
          error={errorFor}
        />

        <EditorFooter
          status={
            dirty && missing
              ? t('upstreams.editor.missing', { fields: missing })
              : undefined
          }
          cancelTo="/context-gateway/upstreams"
          saveLabel={t(stored ? 'actions.save' : 'upstreams.editor.create')}
          saving={save.isPending}
          disabled={!canSave}
        />
      </form>
      <DeleteUpstreamDialog
        upstream={deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null)
        }}
        onDeleted={() => void navigate({ to: '/context-gateway/upstreams' })}
      />
    </>
  )
}
