import type { ComponentType } from 'react'
import { Cloud, FileDown, FileText, GitBranch, Globe2 } from 'lucide-react'

import type {
  RemoteResourceKind,
  RemoteResourceTypeSelection,
} from '../-lib/resource-source'

type SelectableRemoteResourceKind = Exclude<RemoteResourceTypeSelection, 'auto'>

type RemoteResourceDescriptor = {
  exampleKey: `sourcePicker.${SelectableRemoteResourceKind}Example`
  hidden?: boolean
  icon: ComponentType<{ className?: string }>
  type: SelectableRemoteResourceKind
}

export const REMOTE_RESOURCE_DESCRIPTORS: RemoteResourceDescriptor[] = [
  {
    type: 'feishu',
    icon: FileText,
    exampleKey: 'sourcePicker.feishuExample',
  },
  {
    type: 'git',
    icon: GitBranch,
    exampleKey: 'sourcePicker.gitExample',
  },
  {
    type: 'webPage',
    icon: Globe2,
    exampleKey: 'sourcePicker.webPageExample',
  },
  {
    type: 'remoteFile',
    icon: FileDown,
    exampleKey: 'sourcePicker.remoteFileExample',
  },
  {
    type: 'tos',
    hidden: true,
    icon: Cloud,
    exampleKey: 'sourcePicker.tosExample',
  },
]

export const VISIBLE_REMOTE_RESOURCE_DESCRIPTORS =
  REMOTE_RESOURCE_DESCRIPTORS.filter(({ hidden }) => !hidden)

const DESCRIPTOR_BY_TYPE = new Map(
  REMOTE_RESOURCE_DESCRIPTORS.map((descriptor) => [
    descriptor.type,
    descriptor,
  ]),
)

export function getRemoteResourceDescriptor(kind: RemoteResourceKind) {
  const selectableKind = kind === 'webFeed' ? 'webPage' : kind
  return selectableKind === 'unknown'
    ? undefined
    : DESCRIPTOR_BY_TYPE.get(selectableKind)
}
