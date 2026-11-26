import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
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

import type { VikingFsEntry } from '#/shared/types/viking-fs'
import { cn } from '#/shared/lib/utils'
import type { LiveContextMount } from '../-lib/model'
import {
  getAbnormalMountDirectories,
  getLiveMountStatus,
  mountStatusStyles,
} from '../-lib/mount-status'
import { normalizeVikingUri, parentVikingUri } from '../-lib/uri'
import { DirectoryMountWarning } from './mount-status'

type GraphData = {
  entry: VikingFsEntry
  expanded: boolean
  liveMount?: LiveContextMount
  hasAbnormalMounts: boolean
  label: string
  onToggle: (uri: string) => void
}

type ContextGraphProps = {
  entries: VikingFsEntry[]
  expanded: Set<string>
  liveMounts: Map<string, LiveContextMount>
  onSelect: (entry: VikingFsEntry) => void
  onToggle: (uri: string) => void
  root: VikingFsEntry
  selectedUri?: string
}

function ResourceNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('arranger')
  const value = data as GraphData
  const status = value.liveMount ? getLiveMountStatus(value.liveMount) : undefined
  const title = status
    ? [
        t(`live.statusLabels.${status}`),
        t(`live.statusDescriptions.${status}`),
        status !== 'VALID' && value.liveMount?.reason,
      ]
        .filter(Boolean)
        .join('\n')
    : undefined
  const Icon: LucideIcon = value.liveMount
    ? CableIcon
    : value.entry.isDir
      ? FolderIcon
      : FileTextIcon
  return (
    <div
      title={title}
      className={cn(
        'flex h-11 w-44 items-center gap-2 rounded-md border bg-background px-3 text-xs shadow-sm',
        selected && 'border-primary ring-2 ring-primary/15',
      )}
    >
      <Handle type="target" position={Position.Left} className="opacity-0" />
      <Icon
        className={cn(
          'size-4 shrink-0',
          status
            ? mountStatusStyles[status].icon
            : 'text-muted-foreground',
        )}
      />
      <span className="min-w-0 flex-1 truncate">{value.label}</span>
      {value.hasAbnormalMounts && <DirectoryMountWarning />}
      {value.entry.isDir && (
        <button
          type="button"
          className="nodrag nopan -mr-1 flex size-6 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:bg-muted"
          aria-label={t(
            value.expanded
              ? 'actions.collapseDirectory'
              : 'actions.expandDirectory',
            { name: value.label },
          )}
          aria-expanded={value.expanded}
          onClick={(event) => {
            event.stopPropagation()
            value.onToggle(value.entry.uri)
          }}
          onDoubleClick={(event) => event.stopPropagation()}
        >
          {value.expanded ? (
            <ChevronDownIcon className="size-3.5" />
          ) : (
            <ChevronRightIcon className="size-3.5" />
          )}
        </button>
      )}
      <Handle type="source" position={Position.Right} className="opacity-0" />
    </div>
  )
}

const nodeTypes = { resource: ResourceNode }

export function ContextGraph({
  entries,
  expanded,
  liveMounts,
  onSelect,
  onToggle,
  root,
  selectedUri,
}: ContextGraphProps) {
  const { edges, nodes } = useMemo(() => {
    const abnormalDirectories = getAbnormalMountDirectories(liveMounts, root.uri)
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
            liveMount: liveMounts.get(id),
            hasAbnormalMounts: entry.isDir && abnormalDirectories.has(id),
            label: entry.name || entry.uri,
            onToggle,
          } satisfies GraphData,
          position: { x: position.x - 88, y: position.y - 22 },
          selected:
            selectedUri !== undefined &&
            normalizeVikingUri(entry.uri) === normalizeVikingUri(selectedUri),
          type: 'resource',
        }
      }),
    }
  }, [entries, expanded, liveMounts, onToggle, root, selectedUri])

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
      zoomOnDoubleClick={false}
      onNodeClick={(_, node) => {
        const entry = (node.data as GraphData).entry
        onSelect(entry)
      }}
      onNodeDoubleClick={(_, node) => {
        const entry = (node.data as GraphData).entry
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
