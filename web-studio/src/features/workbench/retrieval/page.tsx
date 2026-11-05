import { getRouteApi, useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { RetrievalControls } from '#/features/workbench/retrieval/components/retrieval-controls'
import { RetrievalResults } from '#/features/workbench/retrieval/components/retrieval-results'
import { RetrievalSearchBar } from '#/features/workbench/retrieval/components/search-bar'
import {
  DEFAULT_CUSTOM_PATH_INPUT,
  DEFAULT_RESULT_COUNT,
  DEFAULT_RETRIEVAL_MODE,
  DEFAULT_RETRIEVAL_SCOPE,
} from '#/features/workbench/retrieval/constants/retrieval'
import { useResourceContextProbe } from '#/features/workbench/retrieval/hooks/use-resource-context-probe'
import { useRetrievalQuery } from '#/features/workbench/retrieval/hooks/use-retrieval-query'
import { resolveScopeTargetUri } from '#/features/workbench/retrieval/lib/scope'
import {
  buildSearchFromOptions,
  createRetrievalSubmission,
  hasRetrievalSearch,
  parseCsv,
  parseLevels,
  readLastRetrievalSearch,
  writeLastRetrievalSearch,
} from '#/features/workbench/retrieval/lib/search-state'
import type {
  RetrievalMode,
  RetrievalRequestOptions,
  RetrievalSearch,
} from '#/features/workbench/retrieval/types/retrieval'
import type { FindContextType } from '#/shared/api/retrieval'

const routeApi = getRouteApi('/retrieval')

const FIND_CONTEXT_TYPES = new Set<FindContextType>([
  'memory',
  'resource',
  'skill',
])

function optionsFromSearch(search: RetrievalSearch): RetrievalRequestOptions {
  const scope = search.scope ?? DEFAULT_RETRIEVAL_SCOPE
  const customPathInput = search.path ?? DEFAULT_CUSTOM_PATH_INPUT

  return {
    contextTypes: parseCsv(search.types).filter(
      (type): type is FindContextType =>
        FIND_CONTEXT_TYPES.has(type as FindContextType),
    ),
    customPathInput,
    ignoreCase: search.ignoreCase ?? false,
    includeProvenance: search.provenance ?? false,
    levels: parseLevels(search.levels),
    resultCount: search.count ?? DEFAULT_RESULT_COUNT,
    scoreThreshold: search.minScore,
    sessionId: search.session,
    since: search.since,
    scope,
    tags: parseCsv(search.tags),
    targetUri: resolveScopeTargetUri(scope, customPathInput),
    timeField: search.timeField ?? 'updated_at',
    until: search.until,
  }
}

export function RetrievalPage() {
  const { t } = useTranslation('retrieval')
  const navigate = useNavigate({ from: '/retrieval' })
  const search = routeApi.useSearch()
  const hasUrlSearch = hasRetrievalSearch(search)
  const restoredSearch = useMemo(
    () => (hasUrlSearch ? undefined : readLastRetrievalSearch()),
    [hasUrlSearch],
  )
  const activeSearch = hasUrlSearch ? search : (restoredSearch ?? search)
  const initialMode = activeSearch.mode ?? DEFAULT_RETRIEVAL_MODE
  const initialOptions = useMemo(
    () => optionsFromSearch(activeSearch),
    [activeSearch],
  )

  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>(initialMode)
  const [submittedMode, setSubmittedMode] = useState<RetrievalMode>(initialMode)
  const [query, setQuery] = useState(activeSearch.q ?? '')
  const [submittedQuery, setSubmittedQuery] = useState(activeSearch.q ?? '')
  const [options, setOptions] =
    useState<RetrievalRequestOptions>(initialOptions)
  const [submittedOptions, setSubmittedOptions] =
    useState<RetrievalRequestOptions>(initialOptions)
  const inputRef = useRef<HTMLInputElement>(null)

  const hasSubmitted = submittedQuery.trim().length > 0
  const retrievalQuery = useRetrievalQuery({
    enabled: hasSubmitted,
    mode: submittedMode,
    options: submittedOptions,
    query: submittedQuery,
  })
  const resourceProbeQuery = useResourceContextProbe()

  const handleOptionsChange = useCallback(
    (patch: Partial<RetrievalRequestOptions>) => {
      setOptions((current) => {
        const next = { ...current, ...patch }
        return {
          ...next,
          targetUri: resolveScopeTargetUri(next.scope, next.customPathInput),
        }
      })
    },
    [],
  )

  const submitQuery = useCallback(
    (mode: RetrievalMode) => {
      const submission = createRetrievalSubmission(query, mode, options)
      if (!submission) return

      setSubmittedMode(mode)
      setSubmittedOptions(options)
      setSubmittedQuery(submission.query)
      writeLastRetrievalSearch(submission.search)
      void navigate({ replace: true, search: submission.search })
    },
    [navigate, options, query],
  )

  const handleSubmit = useCallback(() => {
    submitQuery(retrievalMode)
  }, [retrievalMode, submitQuery])

  const handleModeChange = useCallback(
    (nextMode: RetrievalMode) => {
      if (nextMode === retrievalMode) return

      setRetrievalMode(nextMode)
      submitQuery(nextMode)
    },
    [retrievalMode, submitQuery],
  )

  const handleUploadClick = useCallback(() => {
    void navigate({ to: '/filesystem', search: { upload: true } })
  }, [navigate])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!activeSearch.q) return

    const nextMode = activeSearch.mode ?? DEFAULT_RETRIEVAL_MODE
    const nextOptions = optionsFromSearch(activeSearch)
    setRetrievalMode(nextMode)
    setSubmittedMode(nextMode)
    setQuery(activeSearch.q)
    setSubmittedQuery(activeSearch.q)
    setOptions(nextOptions)
    setSubmittedOptions(nextOptions)

    const nextSearch = buildSearchFromOptions(
      activeSearch.q,
      nextMode,
      nextOptions,
    )
    writeLastRetrievalSearch(nextSearch)

    if (!hasUrlSearch) {
      void navigate({ replace: true, search: nextSearch })
    }
  }, [activeSearch, hasUrlSearch, navigate])

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <RetrievalSearchBar
        inputRef={inputRef}
        onChange={setQuery}
        onSubmit={handleSubmit}
        placeholder={t(`placeholders.${retrievalMode}`)}
        query={query}
        submitLabel={t('send')}
      />

      <RetrievalControls
        mode={retrievalMode}
        onModeChange={handleModeChange}
        onOptionsChange={handleOptionsChange}
        options={options}
        t={t}
      />

      <RetrievalResults
        data={hasSubmitted ? retrievalQuery.data : undefined}
        hasRetrievableContext={resourceProbeQuery.data?.hasContext ?? false}
        hasSubmitted={hasSubmitted}
        error={retrievalQuery.error}
        isCheckingContext={resourceProbeQuery.isLoading}
        isError={retrievalQuery.isError}
        isLoading={retrievalQuery.isLoading}
        onUploadClick={handleUploadClick}
        resultCount={submittedOptions.resultCount}
        t={t}
      />
    </div>
  )
}
