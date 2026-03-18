import React, { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { resourceService } from '../services/resources'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card'
import { Button } from '../components'

type ContentLevel = 'l0' | 'l1' | 'l2'

const ResourceDetail: React.FC = () => {
  const { uri } = useParams()
  const navigate = useNavigate()
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [contentLevel, setContentLevel] = useState<ContentLevel>('l2')

  useEffect(() => {
    if (uri) {
      loadResource()
    }
  }, [uri, contentLevel])

  const loadResource = async () => {
    try {
      setLoading(true)
      setError('')
      setContent('')
      if (uri) {
        const decodedUri = decodeURIComponent(uri)
        let response

        if (contentLevel === 'l0') {
          response = await resourceService.getAbstract(decodedUri)
        } else if (contentLevel === 'l1') {
          response = await resourceService.getOverview(decodedUri)
        } else {
          response = await resourceService.read(decodedUri)
        }

        if (response.success && response.data) {
          // data is ContentLevel { uri, content, tokens }
          setContent(response.data.content || '')
        } else if (response.error) {
          // Handle error - response.error is a string
          setError(typeof response.error === 'string' ? response.error : 'Failed to load content')
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load resource')
    } finally {
      setLoading(false)
    }
  }

  if (!uri) {
    return <div className="text-center py-8">Invalid resource URI</div>
  }

  const decodedUri = decodeURIComponent(uri)

  return (
    <div className="p-6 space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold text-gray-800">Resource Details</h1>
        <Button onClick={() => navigate('/resources')}>
          Back to Resources
        </Button>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="font-mono text-sm">{decodedUri}</CardTitle>
            <div className="flex gap-2">
              <Button
                variant={contentLevel === 'l0' ? 'primary' : 'secondary'}
                size="small"
                onClick={() => setContentLevel('l0')}
              >
                L0 Abstract
              </Button>
              <Button
                variant={contentLevel === 'l1' ? 'primary' : 'secondary'}
                size="small"
                onClick={() => setContentLevel('l1')}
              >
                L1 Overview
              </Button>
              <Button
                variant={contentLevel === 'l2' ? 'primary' : 'secondary'}
                size="small"
                onClick={() => setContentLevel('l2')}
              >
                L2 Full
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-center py-8 text-gray-500">Loading...</div>
          ) : error ? (
            <div className="bg-yellow-50 text-yellow-800 px-4 py-3 rounded-lg">
              <p className="font-medium">Note: {error}</p>
              <p className="text-sm mt-1 text-yellow-700">
                L0/L1 content levels are only available for directories. Use L2 Full to view file content.
              </p>
            </div>
          ) : (
            <div className="border rounded-lg p-4 max-h-[600px] overflow-y-auto bg-gray-50">
              <pre className="whitespace-pre-wrap text-sm text-gray-900 font-mono">
                {content || 'No content available'}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export default ResourceDetail
