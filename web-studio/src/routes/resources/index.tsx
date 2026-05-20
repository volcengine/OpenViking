import { createFileRoute, useNavigate } from '@tanstack/react-router'

import { VikingFileManager } from './-components/viking-file-manager'
import { normalizeDirUri, parentUri as getParentUri } from './-lib/normalize'

type ResourcesSearch = {
  uri?: string
  file?: string
  upload?: boolean
}

function shouldPreserveFile(file: unknown, uri: string): file is string {
  return typeof file === 'string' && getParentUri(file) === normalizeDirUri(uri)
}

export const Route = createFileRoute('/resources/')({
  validateSearch: (search: Record<string, unknown>): ResourcesSearch => ({
    uri: typeof search.uri === 'string' ? search.uri : undefined,
    file: typeof search.file === 'string' ? search.file : undefined,
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
      initialFile={search.file}
      initialUploadOpen={search.upload}
      onUriChange={(uri) => {
        navigate({
          search: (prev) => ({
            ...prev,
            uri,
            file: shouldPreserveFile(prev.file, uri) ? prev.file : undefined,
            upload: undefined,
          }),
          replace: true,
        })
      }}
    />
  )
}
