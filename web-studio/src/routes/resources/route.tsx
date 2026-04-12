import { createFileRoute, useNavigate } from '@tanstack/react-router'

import { VikingFileManager } from './-components/viking-file-manager'

type ResourcesSearch = {
  uri?: string
}

export const Route = createFileRoute('/resources')({
  validateSearch: (search: Record<string, unknown>): ResourcesSearch => ({
    uri: typeof search.uri === 'string' ? search.uri : undefined,
  }),
  component: ResourcesRoute,
})

function ResourcesRoute() {
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })

  return (
    <VikingFileManager
      initialUri={search.uri}
      onUriChange={(uri) => {
        navigate({
          search: (prev) => ({ ...prev, uri }),
          replace: true,
        })
      }}
    />
  )
}