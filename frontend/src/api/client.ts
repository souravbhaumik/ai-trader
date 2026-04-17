import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import { useAuthStore } from '../store/authStore'

const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export const apiClient = axios.create({
  baseURL: `${BASE_URL}/api/v1`,
  withCredentials: true, // send httpOnly refresh-token cookie
  timeout: 15_000,
})

// ── Request interceptor: attach Bearer token ──────────────────────────────────
apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = useAuthStore.getState().accessToken
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ── Response interceptor: auto-refresh on 401 ─────────────────────────────────
let isRefreshing = false
let failedQueue: Array<{
  resolve: (token: string) => void
  reject: (err: unknown) => void
}> = []

function processQueue(err: unknown, token: string | null) {
  failedQueue.forEach(({ resolve, reject }) => {
    if (err) reject(err)
    else resolve(token!)
  })
  failedQueue = []
}

apiClient.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean
    }

    if (error.response?.status !== 401 || originalRequest._retry) {
      return Promise.reject(error)
    }

    // Don't retry on /auth/* endpoints (login, refresh, logout)
    if (originalRequest.url?.includes('/auth/')) {
      useAuthStore.getState().clearAuth()
      return Promise.reject(error)
    }

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        failedQueue.push({ resolve, reject })
      }).then((token) => {
        originalRequest.headers.Authorization = `Bearer ${token}`
        return apiClient(originalRequest)
      })
    }

    originalRequest._retry = true
    isRefreshing = true

    try {
      const { data } = await apiClient.post<{ access_token: string; user: unknown }>(
        '/auth/refresh',
      )
      const newToken = data.access_token
      useAuthStore.getState().setAccessToken(newToken)
      processQueue(null, newToken)
      originalRequest.headers.Authorization = `Bearer ${newToken}`
      return apiClient(originalRequest)
    } catch (refreshErr) {
      processQueue(refreshErr, null)
      useAuthStore.getState().clearAuth()
      return Promise.reject(refreshErr)
    } finally {
      isRefreshing = false
    }
  },
)
