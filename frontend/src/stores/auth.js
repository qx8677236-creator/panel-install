import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { raw } from '../api/raw'

const REFRESH_KEY = 'panel_refresh_token'
const USER_KEY = 'panel_username'

export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref('')
  const username = ref(sessionStorage.getItem(USER_KEY) || '')
  const isLoggedIn = computed(() => Boolean(accessToken.value))
  let refreshing = null

  function setSession(data) {
    accessToken.value = data.access_token
    username.value = data.username
    sessionStorage.setItem(REFRESH_KEY, data.refresh_token)
    sessionStorage.setItem(USER_KEY, data.username)
  }

  function clear() {
    accessToken.value = ''
    username.value = ''
    sessionStorage.removeItem(REFRESH_KEY)
    sessionStorage.removeItem(USER_KEY)
  }

  async function login(name, password) {
    const { data } = await raw.post('/api/auth/login', {
      username: name,
      password,
    })
    setSession(data)
  }

  async function refresh() {
    if (refreshing) return refreshing
    const refreshToken = sessionStorage.getItem(REFRESH_KEY)
    if (!refreshToken) {
      throw new Error('缺少刷新令牌')
    }
    refreshing = raw
      .post('/api/auth/refresh', { refresh_token: refreshToken })
      .then(({ data }) => {
        setSession(data)
        return data.access_token
      })
      .finally(() => {
        refreshing = null
      })
    return refreshing
  }

  async function ensureAccessToken() {
    if (accessToken.value) return accessToken.value
    if (!sessionStorage.getItem(REFRESH_KEY)) return ''
    try {
      return await refresh()
    } catch {
      clear()
      return ''
    }
  }

  async function logout() {
    const refreshToken = sessionStorage.getItem(REFRESH_KEY)
    try {
      if (refreshToken) {
        await raw.post('/api/auth/logout', { refresh_token: refreshToken })
      }
    } finally {
      clear()
    }
  }

  return {
    accessToken,
    username,
    isLoggedIn,
    login,
    refresh,
    ensureAccessToken,
    logout,
    clear,
  }
})
