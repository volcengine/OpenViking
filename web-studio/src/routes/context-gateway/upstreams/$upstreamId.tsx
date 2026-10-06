import { createFileRoute } from '@tanstack/react-router'

import { UpstreamEditorPage } from '../-components/upstreams-editor'

export const Route = createFileRoute('/context-gateway/upstreams/$upstreamId')({
  component: UpstreamEditorPage,
})
