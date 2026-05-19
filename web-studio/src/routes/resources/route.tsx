import { createFileRoute, Outlet } from '@tanstack/react-router'

import { ResourceUploadProvider } from './-hooks/use-resource-upload'

export const Route = createFileRoute('/resources')({
  validateSearch: (search: Record<string, unknown>) => ({
    ...(typeof search.uri === 'string' && { uri: search.uri }),
    ...(typeof search.file === 'string' && { file: search.file }),
    ...((search.upload === true || search.upload === 'true') && {
      upload: true,
    }),
  }),
  component: ResourcesLayout,
})

function ResourcesLayout() {
  return (
    <ResourceUploadProvider>
      <Outlet />
    </ResourceUploadProvider>
  )
}
