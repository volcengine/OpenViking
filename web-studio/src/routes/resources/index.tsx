import { createFileRoute, useNavigate } from '@tanstack/react-router'

import { VikingFileManager } from './-components/viking-file-manager'

type ResourcesSearch = {
  uri?: string
  upload?: boolean
}

export const Route = createFileRoute('/resources/')({
  validateSearch: (search: Record<string, unknown>): ResourcesSearch => ({
    uri: typeof search.uri === 'string' ? search.uri : undefined,
    upload: search.upload === true || search.upload === 'true',
  }),
  component: ResourcesIndexRoute,
})

function ResourcesIndexRoute() {
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })

  return (
    <VikingFileManager
      initialUri={search.uri}
      initialUploadOpen={search.upload}
      onUriChange={(uri) => {
        navigate({
          search: (prev) => ({ ...prev, uri, upload: undefined }),
          replace: true,
        })
      }}
    />
  )
}
