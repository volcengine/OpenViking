import { useMemo } from 'react'
import dagre from '@dagrejs/dagre'
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
} from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'
import {
  CableIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FileTextIcon,
  FolderIcon,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import '@xyflow/react/dist/style.css'

import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'
import { cn } from '#/lib/utils'
import { normalizeVikingUri, parentVikingUri } from '../-lib/uri'

type GraphData = {
  entry: VikingFsEntry
  expanded: boolean
  isLive: boolean
  label: string
}

type ContextGraphProps = {
  entries: VikingFsEntry[]
  expanded: Set<string>
  isLive: (uri: string) => boolean
  onSelect: (entry: VikingFsEntry) => void
  onToggle: (uri: string) => void
  root: VikingFsEntry
  selectedUri?: string
}

function ResourceNode({ data, selected }: NodeProps) {
  const value = data as GraphData
  const Icon: LucideIcon = value.isLive
    ? CableIcon
    : value.entry.isDir
      ? FolderIcon
      : FileTextIcon
  return (
    <div
      className={cn(
        'flex h-11 w-44 items-center gap-2 rounded-md border bg-background px-3 text-xs shadow-sm',
        selected && 'border-primary ring-2 ring-primary/15',
      )}
    >
      <Handle type="target" position={Position.Left} className="opacity-0" />
      <Icon
        className={cn(
          'size-4 shrink-0',
          value.isLive ? 'text-emerald-600' : 'text-muted-foreground',
        )}
      />
      <span className="truncate">{value.label}</span>
      {value.entry.isDir &&
        (value.expanded ? (
          <ChevronDownIcon className="ml-auto size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRightIcon className="ml-auto size-3.5 shrink-0 text-muted-foreground" />
        ))}
      <Handle type="source" position={Position.Right} className="opacity-0" />
    </div>
  )
}

const nodeTypes = { resource: ResourceNode }

export function ContextGraph({
  entries,
  expanded,
  isLive,
  onSelect,
  onToggle,
  root,
  selectedUri,
}: ContextGraphProps) {
  const { edges, nodes } = useMemo(() => {
    const graph = new dagre.graphlib.Graph()
    graph.setDefaultEdgeLabel(() => ({}))
    graph.setGraph({ rankdir: 'LR', nodesep: 24, ranksep: 64 })
    const allEntries = [root, ...entries]
    for (const entry of allEntries) {
      graph.setNode(normalizeVikingUri(entry.uri), { height: 44, width: 176 })
    }
    const nextEdges = entries
      .filter((entry) => graph.hasNode(parentVikingUri(entry.uri)))
      .map((entry) => {
        const source = parentVikingUri(entry.uri)
        const target = normalizeVikingUri(entry.uri)
        graph.setEdge(source, target)
        return {
          id: `${source}->${target}`,
          source,
          target,
          type: 'smoothstep',
        }
      })
    dagre.layout(graph)
    return {
      edges: nextEdges,
      nodes: allEntries.map((entry) => {
        const id = normalizeVikingUri(entry.uri)
        const position = graph.node(id) as { x: number; y: number }
        return {
          id,
          data: {
            entry,
            expanded: expanded.has(entry.uri),
            isLive: isLive(entry.uri),
            label: entry.name || entry.uri,
          } satisfies GraphData,
          position: { x: position.x - 88, y: position.y - 22 },
          selected:
            selectedUri !== undefined &&
            normalizeVikingUri(entry.uri) === normalizeVikingUri(selectedUri),
          type: 'resource',
        }
      }),
    }
  }, [entries, expanded, isLive, root, selectedUri])

  return (
    <ReactFlow
      key={nodes.map((node) => node.id).join('|')}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      minZoom={0.35}
      maxZoom={1.6}
      nodesDraggable={false}
      onNodeClick={(_, node) => {
        const entry = (node.data as GraphData).entry
        onSelect(entry)
        if (entry.isDir) onToggle(entry.uri)
      }}
      proOptions={{ hideAttribution: true }}
      className="bg-background"
    >
      <Background gap={24} size={1} />
      <Controls showInteractive={false} />
    </ReactFlow>
  )
}
