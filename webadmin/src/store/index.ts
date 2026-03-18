import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { authService, User } from '../services/auth'
import { monitoringService, MonitoringSummary } from '../services/monitoring'

// Error info type
export interface ErrorInfo {
  message: string
  details?: unknown
  timestamp: string
}

// AppState interface
interface AppState {
  // Authentication state
  isAuthenticated: boolean
  apikey: string | null
  user: User | null

  // Monitoring data
  systemStatus: MonitoringSummary['system'] | null
  queueStats: MonitoringSummary['queue'] | null
  resourceStats: MonitoringSummary['resources'] | null
  vikingDBStatus: MonitoringSummary['vikingdb'] | null
  vlmStats: MonitoringSummary['vlm'] | null
  systemInfo: MonitoringSummary['systemInfo'] | null
  lastMonitoringUpdate: string | null

  // Global error
  error: ErrorInfo | null

  // Authentication actions
  login: (apikey: string) => Promise<void>
  logout: () => void
  setUser: (user: User | null) => void
  checkAuth: () => Promise<boolean>

  // Monitoring actions
  refreshMonitoring: () => Promise<void>
  clearMonitoring: () => void
  setMonitoringData: (data: MonitoringSummary) => void

  // Error actions
  setError: (error: ErrorInfo | null) => void
  clearError: () => void
}

// Create store
export const useStore = create<AppState>()(
  persist(
    (set, get) => ({
      // Initial state
      isAuthenticated: false,
      apikey: null,
      user: null,
      systemStatus: null,
      queueStats: null,
      resourceStats: null,
      vikingDBStatus: null,
      vlmStats: null,
      systemInfo: null,
      lastMonitoringUpdate: null,
      error: null,

      // Authentication actions
      login: async (apikey: string) => {
        const response = await authService.login(apikey)
        if (response.success && response.user) {
          set({
            isAuthenticated: true,
            apikey,
            user: response.user
          })
        } else {
          throw new Error(response.message || 'Login failed')
        }
      },

      logout: () => {
        authService.logout()
        set({
          isAuthenticated: false,
          apikey: null,
          user: null,
          systemStatus: null,
          queueStats: null,
          resourceStats: null,
          vikingDBStatus: null,
          vlmStats: null,
          systemInfo: null,
          lastMonitoringUpdate: null,
          error: null
        })
      },

      setUser: (user) => {
        set({ user })
      },

      checkAuth: async () => {
        const { apikey } = get()
        if (!apikey) {
          set({ isAuthenticated: false, user: null })
          return false
        }

        try {
          const response = await authService.login(apikey)
          if (response.success) {
            set({
              isAuthenticated: true,
              user: response.user || null
            })
            return true
          } else {
            set({ isAuthenticated: false, apikey: null, user: null })
            return false
          }
        } catch {
          set({ isAuthenticated: false, apikey: null, user: null })
          return false
        }
      },

      // Monitoring actions
      refreshMonitoring: async () => {
        const response = await monitoringService.getAll()
        if (response.success && response.data) {
          set({
            systemStatus: response.data.system || null,
            queueStats: response.data.queue || null,
            resourceStats: response.data.resources || null,
            vikingDBStatus: response.data.vikingdb || null,
            vlmStats: response.data.vlm || null,
            systemInfo: response.data.systemInfo || null,
            lastMonitoringUpdate: response.data.last_updated || new Date().toISOString()
          })
        }
      },

      clearMonitoring: () => {
        set({
          systemStatus: null,
          queueStats: null,
          resourceStats: null,
          vikingDBStatus: null,
          vlmStats: null,
          systemInfo: null,
          lastMonitoringUpdate: null
        })
      },

      setMonitoringData: (data) => {
        set({
          systemStatus: data.system || null,
          queueStats: data.queue || null,
          resourceStats: data.resources || null,
          vikingDBStatus: data.vikingdb || null,
          vlmStats: data.vlm || null,
          systemInfo: data.systemInfo || null,
          lastMonitoringUpdate: data.last_updated || new Date().toISOString()
        })
      },

      // Error actions
      setError: (error) => {
        if (error) {
          set({
            error: {
              ...error,
              timestamp: new Date().toISOString()
            }
          })
        } else {
          set({ error: null })
        }
      },

      clearError: () => {
        set({ error: null })
      }
    }),
    {
      name: 'webadmin-storage',
      partialize: (state) => ({
        apikey: state.apikey,
        isAuthenticated: state.isAuthenticated
      })
    }
  )
)

export default useStore
