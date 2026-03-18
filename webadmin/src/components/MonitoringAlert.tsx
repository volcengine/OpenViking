import React from 'react'
import { useMonitoring } from '../hooks'
import { StatusIndicator } from './StatusIndicator'

interface MonitoringAlertProps {
  className?: string
}

export const MonitoringAlert: React.FC<MonitoringAlertProps> = ({ className = '' }) => {
  const { data, error } = useMonitoring()

  if (error) {
    return (
      <div className={`bg-red-50 border border-red-200 rounded-lg p-4 ${className}`}>
        <div className="flex items-center">
          <StatusIndicator status="error" size="medium" />
          <div className="ml-3">
            <p className="text-sm font-medium text-red-800">Monitoring Error</p>
            <p className="text-sm text-red-700 mt-1">{error.message}</p>
          </div>
        </div>
      </div>
    )
  }

  if (!data?.system) {
    return null
  }

  const { status, message } = data.system

  // Only show alerts for non-healthy states
  if (status === 'healthy') {
    return null
  }

  const alertColors = {
    warning: 'bg-yellow-50 border-yellow-200',
    error: 'bg-red-50 border-red-200'
  }

  const textColors = {
    warning: 'text-yellow-800',
    error: 'text-red-800'
  }

  return (
    <div className={`${alertColors[status]} border rounded-lg p-4 ${className}`}>
      <div className="flex items-start">
        <StatusIndicator status={status} size="medium" />
        <div className="ml-3 flex-1">
          <p className={`text-sm font-medium ${textColors[status]}`}>
            System {status.charAt(0).toUpperCase() + status.slice(1)}
          </p>
          {message && (
            <p className={`text-sm ${textColors[status]} mt-1`}>{message}</p>
          )}
        </div>
      </div>
    </div>
  )
}

export default MonitoringAlert
