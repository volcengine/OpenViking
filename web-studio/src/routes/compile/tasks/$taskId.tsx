import { CompileDetail } from '#/features/workbench/compile/tasks/$taskId'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/compile/tasks/$taskId')({
  component: CompileDetail,
})
