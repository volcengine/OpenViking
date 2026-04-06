import { createFileRoute } from '@tanstack/react-router'

import { FileSystemPage } from '#/components/data/filesystem-page'

export const Route = createFileRoute('/data/filesystem')({
  component: FileSystemPage,
})
