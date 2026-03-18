import React, { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { filesystemService } from '../services/filesystem'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card'
import { Button } from '../components'
import { useToast } from '../components/ui/Toast'

interface TreeNode {
  uri: string
  name: string
  type: 'file' | 'directory'
  size: number
  abstract?: string
  children?: TreeNode[]
  loaded?: boolean
  expanded?: boolean
}

const ResourceManagement: React.FC = () => {
  const navigate = useNavigate()
  const { addToast } = useToast()
  const [rootNodes, setRootNodes] = useState<TreeNode[]>([
    { uri: 'viking:///', name: 'root', type: 'directory', size: 0, loaded: false, expanded: false }
  ])
  const [loading, setLoading] = useState<string | null>(null)
  const [selectedNode, setSelectedNode] = useState<TreeNode | null>(null)

  const loadChildren = useCallback(async (uri: string) => {
    setLoading(uri)
    try {
      const response = await filesystemService.list(uri, false, 100)
      if (response.success && response.data) {
        return response.data.map(item => ({
          uri: item.uri,
          name: item.name,
          type: item.type,
          size: item.size,
          abstract: item.abstract,
          loaded: false,
          expanded: false
        }))
      }
      return []
    } catch (err) {
      addToast({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to load directory'
      })
      return []
    } finally {
      setLoading(null)
    }
  }, [addToast])

  const toggleNode = async (node: TreeNode, path: number[]) => {
    if (node.type !== 'directory') return

    if (!node.loaded) {
      const children = await loadChildren(node.uri)
      updateNode(path, { loaded: true, expanded: true, children })
    } else {
      updateNode(path, { expanded: !node.expanded })
    }
  }

  const updateNode = (path: number[], updates: Partial<TreeNode>) => {
    setRootNodes(prev => {
      const newNodes = [...prev]
      let current = newNodes
      for (let i = 0; i < path.length; i++) {
        const idx = path[i]
        if (i === path.length - 1) {
          current[idx] = { ...current[idx], ...updates }
        } else {
          current = current[idx].children!
        }
      }
      return newNodes
    })
  }

  const handleView = (node: TreeNode) => {
    setSelectedNode(node)
    navigate(`/resources/${encodeURIComponent(node.uri)}`)
  }

  const handleDelete = async (node: TreeNode) => {
    if (!window.confirm(`Delete ${node.uri}?`)) return
    try {
      await filesystemService.delete(node.uri, node.type === 'directory')
      addToast({
        type: 'success',
        message: 'Resource deleted successfully'
      })
      // Refresh parent
    } catch (err) {
      addToast({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to delete resource'
      })
    }
  }

  const handleRefresh = async () => {
    setLoading('viking:///')
    const children = await loadChildren('viking:///')
    setRootNodes([{
      uri: 'viking:///',
      name: 'root',
      type: 'directory',
      size: 0,
      loaded: true,
      expanded: true,
      children
    }])
  }

  const renderTree = (nodes: TreeNode[], path: number[] = [], depth: number = 0) => {
    return nodes.map((node, index) => {
      const currentPath = [...path, index]
      const isExpanded = node.expanded
      const isLoading = loading === node.uri
      const hasChildren = node.type === 'directory' && node.children && node.children.length > 0

      return (
        <div key={node.uri} className="select-none">
          <div
            className={`flex items-center py-1.5 px-2 hover:bg-gray-100 rounded cursor-pointer group ${
              selectedNode?.uri === node.uri ? 'bg-blue-50' : ''
            }`}
            style={{ paddingLeft: `${depth * 20 + 8}px` }}
            onClick={() => node.type === 'directory' ? toggleNode(node, currentPath) : setSelectedNode(node)}
          >
            {/* Expand/Collapse Icon */}
            <span className="w-5 h-5 flex items-center justify-center mr-1">
              {node.type === 'directory' ? (
                isLoading ? (
                  <svg className="w-4 h-4 animate-spin text-gray-400" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                ) : (
                  <svg
                    className={`w-4 h-4 text-gray-500 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" />
                  </svg>
                )
              ) : (
                <span className="w-4 h-4" />
              )}
            </span>

            {/* Type Icon */}
            <span className="mr-2">
              {node.type === 'directory' ? (
                <svg className={`w-5 h-5 ${isExpanded ? 'text-yellow-500' : 'text-yellow-400'}`} fill="currentColor" viewBox="0 0 20 20">
                  <path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
                </svg>
              ) : (
                <svg className="w-5 h-5 text-gray-400" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
                </svg>
              )}
            </span>

            {/* Name */}
            <span className="flex-1 text-sm text-gray-700 truncate">{node.name}</span>

            {/* Actions */}
            <div className="hidden group-hover:flex items-center gap-1 mr-2">
              <button
                onClick={(e) => { e.stopPropagation(); handleView(node); }}
                className="text-xs text-blue-600 hover:text-blue-800 px-1"
              >
                View
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); handleDelete(node); }}
                className="text-xs text-red-600 hover:text-red-800 px-1"
              >
                Delete
              </button>
            </div>
          </div>

          {/* Children */}
          {isExpanded && hasChildren && (
            <div className="border-l border-gray-200 ml-4">
              {renderTree(node.children!, currentPath, depth + 1)}
            </div>
          )}

          {/* Empty directory message */}
          {isExpanded && node.loaded && (!node.children || node.children.length === 0) && (
            <div
              className="text-xs text-gray-400 italic py-1"
              style={{ paddingLeft: `${(depth + 1) * 20 + 28}px` }}
            >
              (empty)
            </div>
          )}
        </div>
      )
    })
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold text-gray-800">Resource Management</h1>
        <div className="flex gap-2">
          <Button onClick={handleRefresh} loading={loading === 'viking:///'}>
            Refresh
          </Button>
        </div>
      </div>

      {/* Tree View */}
      <Card>
        <CardHeader>
          <CardTitle>Resources Tree</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="max-h-[600px] overflow-y-auto">
            {renderTree(rootNodes)}
          </div>
        </CardContent>
      </Card>

      {/* Selected Node Details */}
      {selectedNode && selectedNode.type === 'file' && (
        <Card>
          <CardHeader>
            <CardTitle>File Details</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div>
                  <span className="text-gray-500">URI:</span>
                  <p className="font-mono text-gray-900 break-all">{selectedNode.uri}</p>
                </div>
                <div>
                  <span className="text-gray-500">Size:</span>
                  <p className="text-gray-900">{selectedNode.size} bytes</p>
                </div>
                <div>
                  <span className="text-gray-500">Type:</span>
                  <p className="text-gray-900 capitalize">{selectedNode.type}</p>
                </div>
              </div>
              {selectedNode.abstract && (
                <div className="mt-4">
                  <span className="text-gray-500 text-sm">Abstract:</span>
                  <p className="text-gray-700 text-sm mt-1 p-3 bg-gray-50 rounded">
                    {selectedNode.abstract}
                  </p>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

export default ResourceManagement
