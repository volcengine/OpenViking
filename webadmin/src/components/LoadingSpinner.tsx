import React from 'react'

export interface LoadingSpinnerProps {
  size?: 'small' | 'medium' | 'large'
  className?: string
  color?: 'blue' | 'gray' | 'white'
}

export const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({
  size = 'medium',
  className = '',
  color = 'blue'
}) => {
  const sizeClasses = {
    small: 'w-4 h-4',
    medium: 'w-8 h-8',
    large: 'w-12 h-12'
  }

  const colorClasses = {
    blue: 'text-blue-500',
    gray: 'text-gray-500',
    white: 'text-white'
  }

  return (
    <div className={`flex items-center justify-center ${className}`}>
      <div
        className={`${sizeClasses[size]} ${colorClasses[color]} animate-spin rounded-full border-2 border-current border-t-transparent`}
      />
    </div>
  )
}

export default LoadingSpinner
