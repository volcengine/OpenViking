import { createFileRoute, useNavigate } from '@tanstack/react-router'

import { VikingFileManager } from '#/components/viking-fm'

type FileSystemSearch = {
  uri?: string
}

export const Route = createFileRoute('/data/filesystem')({
  validateSearch: (search: Record<string, unknown>): FileSystemSearch => ({
    uri: typeof search.uri === 'string' ? search.uri : undefined,
  }),
  component: FileSystemRoute,
})

function FileSystemRoute() {
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
