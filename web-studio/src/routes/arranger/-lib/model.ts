import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'

export type ArrangerView = 'graph' | 'list'

export type LiveContextMount = {
  node_uri: string
  tool_uri: string
  mcp: string
  tool: string
  arguments: Record<string, unknown>
  input_revision?: string
  created_at?: string
  title?: string
  description?: string
  timeout_seconds?: number
  tags?: string[]
}

export type JsonSchema = {
  $ref?: string
  $defs?: Record<string, JsonSchema>
  definitions?: Record<string, JsonSchema>
  type?: string | readonly string[]
  anyOf?: readonly JsonSchema[]
  oneOf?: readonly JsonSchema[]
  allOf?: readonly JsonSchema[]
  const?: unknown
  title?: string
  description?: string
  default?: unknown
  enum?: readonly unknown[]
  properties?: Record<string, JsonSchema>
  required?: readonly string[]
  items?: JsonSchema
  minimum?: number
  maximum?: number
  minItems?: number
  maxItems?: number
  minLength?: number
  maxLength?: number
  pattern?: string
  format?: string
  examples?: readonly unknown[]
  additionalProperties?: boolean | JsonSchema
  if?: JsonSchema
  then?: JsonSchema
  else?: JsonSchema
}

export type McpToolDefinition = {
  name: string
  title?: string
  description?: string
  inputSchema: JsonSchema
  annotations?: Record<string, unknown>
}

export type McpCatalogTool = {
  server: string
  uri: string
  revision: string
  input_revision: string
  tool: McpToolDefinition
}

export type McpCatalogServer = {
  name: string
  root_uri: string
  tools: McpCatalogTool[]
}

export type McpCatalog = {
  complete: boolean
  error?: string
  servers: McpCatalogServer[]
  tools: McpCatalogTool[]
}

export type LiveMountIndex = {
  complete: boolean
  error?: string
  mounts: Map<string, LiveContextMount>
}

export type LoadedDirectory = {
  entries: VikingFsEntry[]
  hasMore: boolean
  nextOffset: number
}

export type TextDocument = {
  bytes: Uint8Array
  content: string
  sha256: string
}

export type MountInput = {
  tool_uri: string
  node_uri: string
  title: string
  description?: string
  timeout_seconds: number
  arguments: Record<string, unknown>
  tags?: string[]
}

export type MutationKind = 'copy' | 'delete' | 'move'
