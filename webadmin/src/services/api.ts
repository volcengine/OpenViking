import axios, { AxiosError, AxiosResponse } from 'axios'

// Backend API base URL - use relative path to work with any backend host
const BACKEND_API_BASE = '/api/v1'

// API Response types
export interface APIResponse<T = unknown> {
  success: boolean
  data?: T
  error?: string
  message?: string
  details?: unknown
}

export interface APIError {
  status: number
  message: string
  details?: unknown
}

// Create axios client with unified configuration
export const apiClient = axios.create({
  baseURL: BACKEND_API_BASE,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Request interceptor - add authentication
apiClient.interceptors.request.use(
  (config) => {
    const apikey = localStorage.getItem('ov_api_key')
    if (apikey) {
      config.headers['X-API-Key'] = apikey
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// Response interceptor - unified error handling
apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError<APIResponse>) => {
    const apiError: APIError = {
      status: error.response?.status || 500,
      message: error.response?.data?.error || error.message || 'Unknown error',
      details: error.response?.data?.details
    }

    // Handle authentication errors
    if (error.response?.status === 401) {
      localStorage.removeItem('ov_api_key')
      localStorage.removeItem('ov_username')
      window.location.href = '/login'
    }

    // Handle forbidden errors
    if (error.response?.status === 403) {
      // Could add specific handling for permission errors
    }

    return Promise.reject(apiError)
  }
)

// Helper function to handle API responses
export async function handleAPI<T>(
  promise: Promise<AxiosResponse>
): Promise<APIResponse<T>> {
  try {
    const response = await promise
    // Backend returns { status: "ok", result: {...} } format
    // Extract result field if present
    const data = response.data?.result !== undefined ? response.data.result : response.data
    return {
      success: true,
      data: data as T
    }
  } catch (error) {
    const apiError = error as APIError
    return {
      success: false,
      error: apiError.message,
      details: apiError.details
    }
  }
}

export default apiClient
