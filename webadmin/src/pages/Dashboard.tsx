import React, { useState, useEffect } from 'react'
import { useResources, useMonitoring, useTaskStats } from '../hooks'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card'
import { StatusIndicator } from '../components/StatusIndicator'
import { MonitoringAlert } from '../components/MonitoringAlert'
import { LoadingSpinner } from '../components/LoadingSpinner'
import { Button } from '../components/ui/Button'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer
} from 'recharts'

interface DashboardStats {
  totalResources: number
  totalSize: number
  queueLength: number
  activeTasks: number
  failedTasks: number
}

const Dashboard: React.FC = () => {
  const { data: monitoringData, isLoading: isMonitoringLoading, refetch } = useMonitoring({ enabled: true, refetchInterval: 60000 })
  const { data: taskStats, isLoading: isTaskLoading } = useTaskStats()
  const { data: resourcesData, isLoading: isResourcesLoading } = useResources({ limit: 1000 })
  const [stats, setStats] = useState<DashboardStats>({
    totalResources: 0,
    totalSize: 0,
    queueLength: 0,
    activeTasks: 0,
    failedTasks: 0
  })

  useEffect(() => {
    if (resourcesData?.success && resourcesData.data && resourcesData.data.length > 0) {
      const totalSize = resourcesData.data.reduce((sum: number, r: any) => sum + (r.size || 0), 0)
      setStats(prev => ({
        ...prev,
        totalResources: resourcesData.data!.length,
        totalSize
      }))
    }
  }, [resourcesData?.data])

  useEffect(() => {
    if (monitoringData) {
      setStats(prev => ({
        ...prev,
        queueLength:
          monitoringData.queue?.embedding_queue?.queue_length ||
          monitoringData.queue?.semantic_queue?.queue_length ||
          0
      }))
    }
  }, [monitoringData])

  useEffect(() => {
    if (taskStats?.success && taskStats.data) {
      setStats(prev => ({
        ...prev,
        activeTasks: taskStats.data!.running ?? 0,
        failedTasks: taskStats.data!.failed ?? 0
      }))
    }
  }, [taskStats])

  const queueData = monitoringData?.queue
    ? [
        { name: 'Embedding', length: monitoringData.queue.embedding_queue?.queue_length ?? 0 },
        { name: 'Semantic', length: monitoringData.queue.semantic_queue?.queue_length ?? 0 }
      ]
    : []

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
  }

  const isLoading = isMonitoringLoading || isResourcesLoading || isTaskLoading

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner size="large" />
      </div>
    )
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-800">System Dashboard</h1>
        <Button onClick={() => refetch()} size="small">
          Refresh
        </Button>
      </div>

      {/* System Status Alert */}
      <MonitoringAlert />

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* System Status Card */}
        <Card>
          <CardHeader>
            <CardTitle>System Status</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center space-x-2">
              <StatusIndicator
                status={monitoringData?.system?.status || 'unknown'}
                size="large"
              />
              <span className="text-lg font-medium capitalize">
                {monitoringData?.system?.status || 'Unknown'}
              </span>
            </div>
            {monitoringData?.system?.message && (
              <p className="text-sm text-gray-600 mt-2">
                {monitoringData.system.message}
              </p>
            )}
            <p className="text-xs text-gray-500 mt-2">
              Last updated:{' '}
              {monitoringData?.last_updated
                ? new Date(monitoringData.last_updated).toLocaleString()
                : 'N/A'}
            </p>
          </CardContent>
        </Card>

        {/* Resource Stats Card */}
        <Card>
          <CardHeader>
            <CardTitle>Resources</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {stats.totalResources.toLocaleString()} resources
            </div>
            <div className="text-sm text-gray-600 mt-1">
              {formatSize(stats.totalSize)} total
            </div>
          </CardContent>
        </Card>

        {/* Queue Status Card */}
        <Card>
          <CardHeader>
            <CardTitle>Queue Status</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.queueLength} items</div>
            <div className="text-sm text-gray-600 mt-1">
              {monitoringData?.queue?.embedding_queue?.processing ? (
                <span className="text-green-500">Processing</span>
              ) : (
                <span className="text-gray-500">Idle</span>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Task Status Card */}
        <Card>
          <CardHeader>
            <CardTitle>Tasks</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.activeTasks} active</div>
            <div className="text-sm text-gray-600 mt-1">
              {stats.failedTasks} failed
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Queue Chart */}
      <Card>
        <CardHeader>
          <CardTitle>Queue Length</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={queueData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="length" fill="#0088FE" />
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* VikingDB Status */}
      {monitoringData?.vikingdb && (
        <Card>
          <CardHeader>
            <CardTitle>VikingDB Status</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead>
                  <tr className="bg-gray-50">
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Collection</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Index Count</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Vector Count</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                  </tr>
                </thead>
                {monitoringData.vikingdb.collection_list && monitoringData.vikingdb.collection_list.length > 0 && (
                  <>
                    <tbody className="bg-white divide-y divide-gray-200">
                      {monitoringData.vikingdb.collection_list.map((col: any, index: number) => (
                        <tr key={index} className={index % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                          <td className="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">{col.collection}</td>
                          <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900 font-mono">
                            {col.index_count?.toLocaleString() ?? '0'}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900 font-bold font-mono">
                            {col.vector_count?.toLocaleString() ?? '0'}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            <span className={`px-2 py-1 text-xs font-medium rounded-full ${
                              col.status === 'OK' ? 'bg-green-100 text-green-800' :
                              col.status === 'Error' ? 'bg-red-100 text-red-800' :
                              'bg-gray-100 text-gray-800'
                            }`}>
                              {col.status}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot className="bg-gray-100">
                      <tr>
                        <td className="px-4 py-3 text-sm font-medium text-gray-900">Totals:</td>
                        <td className="px-4 py-3 whitespace-nowrap text-sm font-bold text-gray-900 font-mono">
                          {monitoringData.vikingdb.collection_list.reduce((sum: number, c: any) => sum + (c.index_count || 0), 0).toLocaleString()}
                        </td>
                        <td className="px-4 py-3 whitespace-nowrap text-sm font-bold text-gray-900 font-mono">
                          {monitoringData.vikingdb.total_vectors?.toLocaleString() ?? '0'}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500"></td>
                      </tr>
                    </tfoot>
                  </>
                )}
              </table>
              {(!monitoringData.vikingdb.collection_list || monitoringData.vikingdb.collection_list.length === 0) && (
                <div className="text-center py-4 text-gray-500 text-sm">No collection data available</div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* VLM Status */}
      {monitoringData?.vlm && (
        <Card>
          <CardHeader>
            <CardTitle>VLM Status</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead>
                  <tr className="bg-gray-50">
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Model</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Provider</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Prompt Tokens</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Completion Tokens</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Total Tokens</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Last Updated</th>
                  </tr>
                </thead>
                {monitoringData.vlm.models && monitoringData.vlm.models.length > 0 && (
                  <>
                    <tbody className="bg-white divide-y divide-gray-200">
                      {monitoringData.vlm.models.map((model: any, index: number) => (
                        <tr key={index} className={index % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                          <td className="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">{model.model}</td>
                          <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-600">{model.provider}</td>
                          <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900 font-mono">
                            {model.prompt_tokens?.toLocaleString() ?? '0'}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900 font-mono">
                            {model.completion_tokens?.toLocaleString() ?? '0'}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900 font-bold font-mono">
                            {model.total_tokens?.toLocaleString() ?? '0'}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                            {model.last_updated ? new Date(model.last_updated).toLocaleString() : 'N/A'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    {monitoringData.vlm.token_usage && (
                      <tfoot className="bg-gray-100">
                        <tr>
                          <td colSpan={2} className="px-4 py-3 text-sm font-medium text-gray-900 text-right">Totals:</td>
                          <td className="px-4 py-3 whitespace-nowrap text-sm font-bold text-gray-900 font-mono">
                            {monitoringData.vlm.token_usage.prompt_tokens?.toLocaleString() ?? '0'}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap text-sm font-bold text-gray-900 font-mono">
                            {monitoringData.vlm.token_usage.completion_tokens?.toLocaleString() ?? '0'}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap text-sm font-bold text-gray-900 font-mono">
                            {monitoringData.vlm.token_usage.total_tokens?.toLocaleString() ?? '0'}
                          </td>
                          <td className="px-4 py-3 text-sm text-gray-500"></td>
                        </tr>
                      </tfoot>
                    )}
                  </>
                )}
              </table>
              {(!monitoringData.vlm.models || monitoringData.vlm.models.length === 0) && (
                <div className="text-center py-4 text-gray-500 text-sm">No VLM data available</div>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

export default Dashboard
