import React, { useState } from 'react'
import {
  useSearchFind,
  useGrepSearch
} from '../hooks'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card'
import { Button, Input } from '../components'
import { useToast } from '../components/ui/Toast'

type TabType = 'semantic' | 'content'

interface SearchResult {
  uri: string
  context_type: string
  level: number
  abstract: string
  score: number
}

interface GrepResult {
  uri: string
  line_number: number
  line: string
  pattern: string
}

const SemanticSearch: React.FC = () => {
  const [query, setQuery] = useState('')
  const [targetUri, setTargetUri] = useState('viking:///')
  const [limit, setLimit] = useState(10)
  const [activeTab, setActiveTab] = useState<TabType>('semantic')
  const [grepPattern, setGrepPattern] = useState('')
  const { addToast } = useToast()

  const semanticQuery = useSearchFind(query, limit)
  const grepQuery = useGrepSearch(targetUri, grepPattern)

  const flattenResults = (response: any): SearchResult[] => {
    if (!response?.data) return []
    const data = response.data
    return [
      ...(data.resources || []),
      ...(data.memories || []),
      ...(data.skills || [])
    ]
  }

  const handleSemanticSearch = () => {
    // The hook handles the search automatically when query changes
    if (!query.trim()) {
      addToast({
        type: 'warning',
        message: 'Please enter a search query'
      })
    }
  }

  const handleContentSearch = () => {
    if (!grepPattern.trim()) {
      addToast({
        type: 'warning',
        message: 'Please enter a search pattern'
      })
      return
    }

    // If targetUri is empty, use default value and update state
    const effectiveUri = targetUri.trim() || 'viking:///'
    if (effectiveUri !== targetUri) {
      setTargetUri(effectiveUri)
    }
  }

  const formatScore = (score: number): string => {
    return score.toFixed(3)
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <Card>
        <CardHeader>
          <CardTitle>Search</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="border-b">
            <nav className="-mb-px flex space-x-8">
              <button
                onClick={() => setActiveTab('semantic')}
                className={`py-4 px-1 border-b-2 font-medium text-sm ${
                  activeTab === 'semantic'
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                Semantic Search
              </button>
              <button
                onClick={() => setActiveTab('content')}
                className={`py-4 px-1 border-b-2 font-medium text-sm ${
                  activeTab === 'content'
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                Content Search (Grep)
              </button>
            </nav>
          </div>
        </CardContent>
      </Card>

      {/* Search Form */}
      <Card>
        <CardHeader>
          <CardTitle>Search Options</CardTitle>
        </CardHeader>
        <CardContent>
          {activeTab === 'semantic' ? (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Query</label>
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && handleSemanticSearch()}
                  placeholder="Enter search query..."
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Target URI (optional)</label>
                  <Input
                    value={targetUri}
                    onChange={(e) => setTargetUri(e.target.value)}
                    placeholder="viking:///"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Limit</label>
                  <Input
                    type="number"
                    value={limit}
                    onChange={(e) => setLimit(Number(e.target.value))}
                    min="1"
                    max="50"
                  />
                </div>
              </div>
              <Button onClick={handleSemanticSearch}>
                Search
              </Button>
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Target URI</label>
                <Input
                  value={targetUri}
                  onChange={(e) => setTargetUri(e.target.value)}
                  placeholder="viking:///"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Pattern</label>
                <Input
                  value={grepPattern}
                  onChange={(e) => setGrepPattern(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && handleContentSearch()}
                  placeholder="Enter regex pattern..."
                />
              </div>
              <Button onClick={handleContentSearch}>
                Search
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Results */}
      {(semanticQuery.data || grepQuery.data) && (
        <Card>
          <CardHeader>
            <CardTitle>Results</CardTitle>
          </CardHeader>
          <CardContent>
            {activeTab === 'semantic' ? (
              semanticQuery.isLoading ? (
                <div className="text-center py-8 text-gray-500">Searching...</div>
              ) : semanticQuery.error ? (
                <div className="bg-red-50 text-red-600 px-4 py-3 rounded-lg">
                  Error: {semanticQuery.error.message}
                </div>
              ) : flattenResults(semanticQuery.data).length === 0 ? (
                <div className="text-center py-8 text-gray-500">
                  No results found
                </div>
              ) : (
                <div className="space-y-4">
                  <p className="text-sm text-gray-600">
                    Found {flattenResults(semanticQuery.data).length} results
                  </p>
                  <div className="max-h-[600px] overflow-y-auto space-y-4 pr-2">
                    {flattenResults(semanticQuery.data).map((result: SearchResult, index: number) => (
                      <div
                        key={`${result.uri}-${result.context_type}-${result.level}-${index}`}
                        className="border rounded-lg p-4 hover:shadow-md transition-shadow"
                      >
                        <div className="flex justify-between items-start mb-2">
                          <h4 className="font-semibold text-gray-900 font-mono text-sm">
                            {result.uri}
                          </h4>
                          <span className="text-sm text-green-600">
                            Score: {formatScore(result.score)}
                          </span>
                        </div>
                        <div className="text-sm text-gray-500 mb-2 flex gap-4">
                          <span>Type: {result.context_type}</span>
                          <span>Level: {result.level}</span>
                        </div>
                        <p className="text-gray-700 text-sm whitespace-pre-wrap">
                          {result.abstract}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )
            ) : (
              grepQuery.isLoading ? (
                <div className="text-center py-8 text-gray-500">Searching...</div>
              ) : grepQuery.error ? (
                <div className="bg-red-50 text-red-600 px-4 py-3 rounded-lg">
                  Error: {grepQuery.error.message}
                </div>
              ) : grepQuery.data?.success && (grepQuery.data.data?.length || 0) === 0 ? (
                <div className="text-center py-8 text-gray-500">
                  No matches found
                </div>
              ) : (
                <div className="space-y-4">
                  <p className="text-sm text-gray-600">
                    Found {grepQuery.data?.data?.length || 0} matches
                  </p>
                  <div className="max-h-[600px] overflow-y-auto space-y-4 pr-2">
                    {grepQuery.data?.data?.map((result: GrepResult, index: number) => (
                      <div
                        key={`${result.uri}-${result.line_number}-${index}`}
                        className="border rounded-lg p-4 hover:shadow-md transition-shadow"
                      >
                        <div className="flex justify-between items-start mb-2">
                          <h4 className="font-semibold text-gray-900 font-mono text-sm">
                            {result.uri}
                          </h4>
                          <span className="text-sm text-blue-600">
                            Line: {result.line_number}
                          </span>
                        </div>
                        <pre className="bg-gray-50 p-3 rounded text-sm whitespace-pre-wrap font-mono">
                          {result.line}
                        </pre>
                      </div>
                    ))}
                  </div>
                </div>
              )
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

export default SemanticSearch
