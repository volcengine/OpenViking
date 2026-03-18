import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { authService, User } from '../services/auth'

interface AuthContextType {
  isAuthenticated: boolean
  user: User | null
  login: (apikey: string) => Promise<void>
  logout: () => void
  isLoading: boolean
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const verifyAuth = async () => {
      const apikey = localStorage.getItem('ov_api_key')
      const username = localStorage.getItem('ov_username')

      if (!apikey) {
        setIsLoading(false)
        return
      }

      try {
        // Verify API key by calling /system/status
        const response = await fetch('/api/v1/system/status', {
          method: 'GET',
          headers: {
            'X-API-Key': apikey,
            'Content-Type': 'application/json'
          }
        })

        if (response.ok) {
          const data = await response.json()
          if (data.status === 'ok') {
            setIsAuthenticated(true)
            setUser({
              uid: 'current',
              username: username || 'admin',
              role: 'USER'
            })
          } else {
            // Invalid API key
            localStorage.removeItem('ov_api_key')
            localStorage.removeItem('ov_username')
          }
        } else {
          // API error
          localStorage.removeItem('ov_api_key')
          localStorage.removeItem('ov_username')
        }
      } catch (error) {
        // Network error or server down
        console.error('Auth verification failed:', error)
        localStorage.removeItem('ov_api_key')
        localStorage.removeItem('ov_username')
      } finally {
        setIsLoading(false)
      }
    }

    verifyAuth()
  }, [])

  const login = async (apikey: string) => {
    try {
      const response = await authService.login(apikey)
      if (response.success && response.user) {
        localStorage.setItem('ov_api_key', apikey)
        localStorage.setItem('ov_username', response.user.username)
        setIsAuthenticated(true)
        setUser(response.user)
      } else {
        throw new Error(response.message || 'Login failed')
      }
    } catch (error) {
      setIsAuthenticated(false)
      setUser(null)
      throw error
    }
  }

  const logout = () => {
    authService.logout()
    setIsAuthenticated(false)
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, user, login, logout, isLoading }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
