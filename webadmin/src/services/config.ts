export interface ServerConfig {
  host: string
  port: number
  workers: number
  root_api_key?: string
  cors_origins: string[]
  with_bot: boolean
  bot_api_url: string
}

export interface ConfigResponse {
  success: boolean
  config?: ServerConfig
  error?: string
}

// Backend service API base URL
const BACKEND_API_BASE = window.location.origin

export const configService = {
  /**
   * Fetch current server configuration from backend
   */
  async getConfig(): Promise<ConfigResponse> {
    try {
      const response = await fetch(`${BACKEND_API_BASE}/api/config`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json'
        }
      })
      const data = await response.json()
      return data
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Failed to fetch configuration'
      }
    }
  },

  /**
   * Update server configuration via backend
   */
  async updateConfig(config: Partial<ServerConfig>): Promise<ConfigResponse> {
    try {
      const response = await fetch(`${BACKEND_API_BASE}/api/config`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(config)
      })
      const data = await response.json()
      return data
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Failed to update configuration'
      }
    }
  },

  /**
   * Test API connection via backend proxy
   */
  async testConnection(): Promise<{ success: boolean; message: string }> {
    try {
      const response = await fetch(`${BACKEND_API_BASE}/api/health`, {
        method: 'GET'
      })
      const data = await response.json()
      if (data.success) {
        return {
          success: true,
          message: data.message
        }
      }
      return {
        success: false,
        message: data.message
      }
    } catch (error) {
      return {
        success: false,
        message: error instanceof Error ? error.message : 'Connection failed'
      }
    }
  },

  /**
   * Proxy API request to OpenViking server via backend
   */
  async proxyRequest<T>(method: string, path: string, data?: any, headers?: Record<string, string>): Promise<{ success: boolean; data?: T; error?: string }> {
    try {
      const response = await fetch(`${BACKEND_API_BASE}/api/proxy`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ method, path, data, headers })
      })
      const result = await response.json()
      if (result.success) {
        return { success: true, data: result.data }
      }
      return { success: false, error: result.error }
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Request failed'
      }
    }
  }
}
