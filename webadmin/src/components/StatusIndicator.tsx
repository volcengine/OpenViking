import React from 'react'

export interface StatusIndicatorProps {
  status: 'healthy' | 'warning' | 'error' | 'unknown'
  size?: 'small' | 'medium' | 'large'
  label?: string
  className?: string
}

export const StatusIndicator: React.FC<StatusIndicatorProps> = ({
  status,
  size = 'medium',
  label,
  className = ''
}) => {
  const sizeClasses = {
    small: 'w-2 h-2',
    medium: 'w-3 h-3',
    large: 'w-4 h-4'
  }

  const colorClasses = {
    healthy: 'bg-green-500',
    warning: 'bg-yellow-500',
    error: 'bg-red-500',
    unknown: 'bg-gray-400'
  }

  return (
    <div className={`flex items-center ${className}`}>
      <div
        className={`${sizeClasses[size]} ${colorClasses[status]} rounded-full animate-pulse`}
      />
      {label && (
        <span className={`ml-2 text-sm capitalize text-gray-700`}>{label}</span>
      )}
    </div>
  )
}

export default StatusIndicator
