import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { RetrievalControls } from './-components/retrieval-controls'
import { RetrievalResults } from './-components/retrieval-results'
import { RetrievalSearchBar } from './-components/search-bar'
import {
  DEFAULT_CUSTOM_PATH_INPUT,
  DEFAULT_RESULT_COUNT,
  DEFAULT_RETRIEVAL_MODE,
  DEFAULT_RETRIEVAL_SCOPE,
} from './-constants/retrieval'
import { useResourceContextProbe } from './-hooks/use-resource-context-probe'
import { useRetrievalQuery } from './-hooks/use-retrieval-query'
import { flattenResults } from './-lib/results'
import { resolveScopeTargetUri } from './-lib/scope'
import {
  buildSubmittedSearch,
  hasRetrievalSearch,
  readLastRetrievalSearch,
  validateRetrievalSearch,
  writeLastRetrievalSearch,
} from './-lib/search-state'
import type { RetrievalMode, RetrievalScope } from './-types/retrieval'

export const Route = createFileRoute('/retrieval')({
  validateSearch: validateRetrievalSearch,
  component: RetrievalPage,
})

function RetrievalPage() {
  const { t } = useTranslation('retrieval')
  const navigate = useNavigate({ from: Route.fullPath })
  const search = Route.useSearch()
  const hasUrlSearch = hasRetrievalSearch(search)
  const restoredSearch = useMemo(
    () => (hasUrlSearch ? undefined : readLastRetrievalSearch()),
    [hasUrlSearch],
  )
  const activeSearch = hasUrlSearch ? search : (restoredSearch ?? search)

  const initialQuery = activeSearch.q ?? ''
  const initialMode = activeSearch.mode ?? DEFAULT_RETRIEVAL_MODE
  const initialResultCount = activeSearch.count ?? DEFAULT_RESULT_COUNT
  const initialScope = activeSearch.scope ?? DEFAULT_RETRIEVAL_SCOPE
  const initialCustomPath = activeSearch.path ?? DEFAULT_CUSTOM_PATH_INPUT
  const initialSessionId = activeSearch.session ?? ''

  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>(initialMode)
  const [query, setQuery] = useState(initialQuery)
  const [submittedQuery, setSubmittedQuery] = useState(initialQuery)
  const [resultCount, setResultCount] = useState<number>(initialResultCount)
  const [retrievalScope, setRetrievalScope] =
    useState<RetrievalScope>(initialScope)
  const [customPathInput, setCustomPathInput] = useState(initialCustomPath)
  const [sessionIdInput, setSessionIdInput] = useState(initialSessionId)
  const inputRef = useRef<HTMLInputElement>(null)

  const targetUri = useMemo(() => {
    return resolveScopeTargetUri(retrievalScope, customPathInput)
  }, [customPathInput, retrievalScope])

  const hasSubmitted = submittedQuery.trim().length > 0
  const sessionId = sessionIdInput.trim() || undefined
  const retrievalQuery = useRetrievalQuery({
    enabled: hasSubmitted,
    mode: retrievalMode,
    query: submittedQuery,
    resultCount,
    sessionId,
    targetUri,
  })
  const resourceProbeQuery = useResourceContextProbe()

  const data = hasSubmitted ? retrievalQuery.data : undefined
  const hasResults = Boolean(data && data.total > 0)
  const hasRetrievableContext = resourceProbeQuery.data?.hasContext ?? false
  const flatItems = useMemo(() => (data ? flattenResults(data) : []), [data])
  const queryPlanItems = data?.query_plan?.queries ?? []

  const handleSubmit = useCallback(() => {
    const trimmed = query.trim()
    if (trimmed.length === 0) {
      return
    }

    const nextSearch = buildSubmittedSearch({
      count: resultCount,
      mode: retrievalMode,
      path: customPathInput,
      q: trimmed,
      scope: retrievalScope,
      session: sessionIdInput,
    })

    setSubmittedQuery(trimmed)
    writeLastRetrievalSearch(nextSearch)
    void navigate({
      replace: true,
      search: nextSearch,
    })
  }, [
    customPathInput,
    navigate,
    query,
    resultCount,
    retrievalMode,
    retrievalScope,
    sessionIdInput,
  ])

  const handleUploadClick = useCallback(() => {
    void navigate({ to: '/resources', search: { upload: true } })
  }, [navigate])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!activeSearch.q) {
      return
    }

    const nextMode = activeSearch.mode ?? DEFAULT_RETRIEVAL_MODE
    const nextResultCount = activeSearch.count ?? DEFAULT_RESULT_COUNT
    const nextScope = activeSearch.scope ?? DEFAULT_RETRIEVAL_SCOPE
    const nextCustomPath = activeSearch.path ?? DEFAULT_CUSTOM_PATH_INPUT
    const nextSessionId = activeSearch.session ?? ''

    setRetrievalMode(nextMode)
    setQuery(activeSearch.q)
    setSubmittedQuery(activeSearch.q)
    setResultCount(nextResultCount)
    setRetrievalScope(nextScope)
    setCustomPathInput(nextCustomPath)
    setSessionIdInput(nextSessionId)

    const nextSearch = buildSubmittedSearch({
      count: nextResultCount,
      mode: nextMode,
      path: nextCustomPath,
      q: activeSearch.q,
      scope: nextScope,
      session: nextSessionId,
    })

    writeLastRetrievalSearch(nextSearch)

    if (!hasUrlSearch) {
      void navigate({
        replace: true,
        search: nextSearch,
      })
    }
  }, [
    activeSearch.count,
    activeSearch.mode,
    activeSearch.path,
    activeSearch.q,
    activeSearch.scope,
    activeSearch.session,
    hasUrlSearch,
    navigate,
  ])

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <RetrievalSearchBar
        inputRef={inputRef}
        onChange={setQuery}
        onSubmit={handleSubmit}
        placeholder={t('searchPlaceholder')}
        query={query}
      />

      <RetrievalControls
        customPathInput={customPathInput}
        mode={retrievalMode}
        onCustomPathInputChange={setCustomPathInput}
        onModeChange={setRetrievalMode}
        onResultCountChange={setResultCount}
        onScopeChange={setRetrievalScope}
        onSessionIdInputChange={setSessionIdInput}
        resultCount={resultCount}
        scope={retrievalScope}
        sessionIdInput={sessionIdInput}
        t={t}
        targetUri={targetUri}
      />

      <RetrievalResults
        flatItems={flatItems}
        hasRetrievableContext={hasRetrievableContext}
        hasResults={hasResults}
        hasSubmitted={hasSubmitted}
        isCheckingContext={resourceProbeQuery.isLoading}
        isError={retrievalQuery.isError}
        isLoading={retrievalQuery.isLoading}
        onUploadClick={handleUploadClick}
        queryPlanItems={queryPlanItems}
        resultCount={resultCount}
        t={t}
      />
    </div>
  )
}
