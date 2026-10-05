import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import { apiBaseURL, stripApiPrefix } from './origin'
import { readErrorMessage, shouldToast, showError } from '../utils/message'

export const http = axios.create({
  baseURL: apiBaseURL(),
  timeout: 10000,
})

const inflight = new Map()

export function isCanceled(error) {
  return (
    axios.isCancel(error)
    || error?.canceled
    || error?.code === 'ERR_CANCELED'
    || error?.name === 'AbortError'
    || error?.cause?.name === 'AbortError'
  )
}

function attachCancel(config) {
  const key = config.cancelKey
  if (!key) return
  const previous = inflight.get(key)
  if (previous) previous.abort()
  const controller = new AbortController()
  config.signal = controller.signal
  config._abortController = controller
  inflight.set(key, controller)
}

function releaseCancel(config) {
  if (!config?._abortController) return
  if (inflight.get(config.cancelKey) === config._abortController) {
    inflight.delete(config.cancelKey)
  }
}

http.interceptors.request.use((config) => {
  config.url = stripApiPrefix(config.url)
  attachCancel(config)
  const token = useAuthStore().accessToken
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 并发的 401 共用同一次刷新，刷新成功后重放原请求。
let refreshing = null

http.interceptors.response.use(
  (response) => {
    releaseCancel(response.config)
    return response
  },
  async (error) => {
    releaseCancel(error.config)
    if (isCanceled(error)) {
      error.canceled = true
      return Promise.reject(error)
    }
    const original = error.config
    const status = error.response?.status
    if (!original || status !== 401 || original._retry) {
      if (shouldToast(error)) showError(readErrorMessage(error))
      return Promise.reject(error)
    }
    original._retry = true
    try {
      const auth = useAuthStore()
      if (!refreshing) {
        refreshing = auth.refresh().finally(() => {
          refreshing = null
        })
      }
      const token = await refreshing
      original.headers = original.headers || {}
      original.headers.Authorization = `Bearer ${token}`
      return http(original)
    } catch (refreshError) {
      useAuthStore().clear()
      const { default: router } = await import('../router')
      if (router.currentRoute.value.name !== 'login') {
        await router.replace({ name: 'login' })
      }
      if (shouldToast(refreshError)) showError(readErrorMessage(refreshError))
      return Promise.reject(refreshError)
    }
  },
)
