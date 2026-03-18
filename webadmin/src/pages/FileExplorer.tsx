import React, { useState } from 'react'
import {
  useFileSystemList,
  useMkDir,
  useGetAbstract,
  useGetOverview,
  useReadContent
} from '../hooks'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card'
import { Button, Input, Modal } from '../components'
import { useToast } from '../components/ui/Toast'

type ContentLevel = 'l0' | 'l1' | 'l2'

const FileExplorer: React.FC = () => {
  const [currentUri, setCurrentUri] = useState('viking:///')
  const [showContentModal, setShowContentModal] = useState(false)
  const [selectedUri, setSelectedUri] = useState('')
  const [contentLevel, setContentLevel] = useState<ContentLevel>('l0')
  const { addToast } = useToast()

  const { data: files, isLoading, refetch } = useFileSystemList(currentUri, false, 100)
  const mkdirMutation = useMkDir()

  const abstractQuery = useGetAbstract(selectedUri)
  const overviewQuery = useGetOverview(selectedUri)
  const contentQuery = useReadContent(selectedUri)

  const contentData = contentLevel === 'l0' ? abstractQuery.data
    : contentLevel === 'l1' ? overviewQuery.data
    : contentQuery.data

  // contentData is APIResponse<ContentLevel>, extract content from data.data.content
  const content = typeof contentData?.data === 'string' ? contentData.data
    : (contentData?.data as any)?.content || ''

  const handleLoadContent = (uri: string, level: ContentLevel) => {
    setSelectedUri(uri)
    setContentLevel(level)
    setShowContentModal(true)
  }

  const handleMkdir = async () => {
    const name = prompt('Enter directory name:')
    if (!name) return
    try {
      const newUri = `${currentUri}${name}/`
      await mkdirMutation.mutateAsync(newUri)
      addToast({
        type: 'success',
        message: 'Directory created successfully'
      })
      refetch()
    } catch (err) {
      addToast({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to create directory'
      })
    }
  }

  const getIcon = (type: string) => {
    if (type === 'directory') {
      return (
        <svg className="w-5 h-5 text-yellow-500" fill="currentColor" viewBox="0 0 20 20">
          <path d="M3 4a2 2 0 012-2h6a2 2 0 012 2v1h1a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2V4z" />
        </svg>
      )
    }
    return (
      <svg className="w-5 h-5 text-gray-500" fill="currentColor" viewBox="0 0 20 20">
        <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
      </svg>
    )
  }

  // Parse URI into breadcrumb segments
  const getBreadcrumbSegments = (uri: string) => {
    // viking:///path/to/folder/ -> ['viking://', 'path', 'to', 'folder']
    const segments: string[] = []
    const protocolMatch = uri.match(/^(viking:\/\/)/)
    if (protocolMatch) {
      segments.push(protocolMatch[1])
      const rest = uri.slice(protocolMatch[1].length)
      const parts = rest.split('/').filter(p => p !== '')
      segments.push(...parts)
    }
    return segments
  }

  // Build URI from segments up to index
  const buildUriFromSegments = (segments: string[], endIndex: number) => {
    const protocol = segments[0]
    const pathParts = segments.slice(1, endIndex + 1)
    return `${protocol}${pathParts.join('/')}/`
  }

  // Navigate to a specific path segment
  const navigateToSegment = (segments: string[], endIndex: number) => {
    const newUri = buildUriFromSegments(segments, endIndex)
    setCurrentUri(newUri)
    refetch()
  }

  const breadcrumbSegments = getBreadcrumbSegments(currentUri)

  const fileList = files?.success ? files.data || [] : []

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <Card>
        <CardHeader>
          <CardTitle>File Explorer</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-4">
            <Input
              value={currentUri}
              onChange={(e) => setCurrentUri(e.target.value)}
              className="w-96"
              placeholder="viking:///"
            />
            <Button onClick={() => refetch()}>
              Load
            </Button>
            <Button
              onClick={handleMkdir}
              loading={mkdirMutation.isPending}
            >
              Create Directory
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Breadcrumb Navigation */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center flex-wrap gap-1 text-sm">
            {breadcrumbSegments.map((segment, index) => (
              <React.Fragment key={index}>
                {index > 0 && (
                  <svg className="w-4 h-4 text-gray-400" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" />
                  </svg>
                )}
                {index < breadcrumbSegments.length - 1 ? (
                  <button
                    onClick={() => navigateToSegment(breadcrumbSegments, index)}
                    className="text-blue-600 hover:text-blue-800 hover:underline font-medium"
                  >
                    {segment}
                  </button>
                ) : (
                  <span className="text-gray-700 font-medium">{segment}</span>
                )}
              </React.Fragment>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Files Grid */}
      <Card>
        <CardHeader>
          <CardTitle>Files</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="text-center py-8 text-gray-500">Loading...</div>
          ) : fileList.length === 0 ? (
            <div className="text-center py-8 text-gray-500">No files</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {fileList.map((file: any) => (
                <div
                  key={file.uri}
                  className="border rounded-lg p-4 hover:shadow-md transition-shadow overflow-hidden"
                >
                  <div className="flex items-start justify-between mb-2 min-w-0">
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      {getIcon(file.type)}
                      <div className="min-w-0 flex-1">
                        {file.type === 'directory' ? (
                          <button
                            onClick={() => {
                              setCurrentUri(file.uri)
                              refetch()
                            }}
                            className="font-medium text-gray-900 truncate hover:text-blue-600 hover:underline text-left w-full"
                            title={file.uri}
                          >
                            {file.name}
                          </button>
                        ) : (
                          <div className="font-medium text-gray-900 truncate" title={file.uri}>{file.name}</div>
                        )}
                        <div className="text-xs text-gray-500 font-mono truncate" title={file.uri}>{file.uri}</div>
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-2 mt-3">
                    <Button
                      variant="secondary"
                      size="small"
                      onClick={() => handleLoadContent(file.uri, 'l0')}
                    >
                      L0
                    </Button>
                    <Button
                      variant="secondary"
                      size="small"
                      onClick={() => handleLoadContent(file.uri, 'l1')}
                    >
                      L1
                    </Button>
                    <Button
                      variant="secondary"
                      size="small"
                      onClick={() => handleLoadContent(file.uri, 'l2')}
                    >
                      L2
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Content Modal */}
      <Modal
        isOpen={showContentModal}
        onClose={() => setShowContentModal(false)}
        title={`Content (${contentLevel.toUpperCase()})`}
        size="large"
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-gray-600 font-mono">{selectedUri}</p>
            <span className={`px-2 py-1 rounded text-xs font-medium ${
              contentLevel === 'l0' ? 'bg-blue-100 text-blue-700'
              : contentLevel === 'l1' ? 'bg-green-100 text-green-700'
              : 'bg-purple-100 text-purple-700'
            }`}>
              {contentLevel === 'l0' ? 'Abstract'
                : contentLevel === 'l1' ? 'Overview'
                : 'Full Content'}
            </span>
          </div>

          {(abstractQuery.isLoading || overviewQuery.isLoading || contentQuery.isLoading) ? (
            <div className="text-center py-8 text-gray-500">Loading content...</div>
          ) : (
            <div className="border rounded-lg p-4 max-h-96 overflow-y-auto">
              <pre className="whitespace-pre-wrap text-sm text-gray-900 font-mono">
                {content || 'No content available'}
              </pre>
            </div>
          )}
        </div>
      </Modal>
    </div>
  )
}

export default FileExplorer
