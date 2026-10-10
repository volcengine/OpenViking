import { createFileRoute } from '@tanstack/react-router'

import { UpstreamEditorPage } from '../-components/upstreams-editor'

export const Route = createFileRoute('/gateway/upstreams/$upstreamId')({
  component: UpstreamEditorPage,
})
