import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { http } from '../api/http'
import { useAuthStore } from './auth'

export const useMetricsStore = defineStore('metrics', () => {
  const history = ref([])
  const status = ref('idle')
  const current = computed(() => history.value[history.value.length - 1] || null)

  let socket = null
  let stopped = true
  let ticket = 0
  let attempts = 0
  let authRetries = 0
  let timer = 0

  function push(point) {
    if (!point || typeof point.ts !== 'number') return
    const items = history.value
    const last = items[items.length - 1]
    if (last && last.ts === point.ts) {
      items[items.length - 1] = point
      return
    }
    items.push(point)
    if (items.length > 120) items.shift()
  }

  function connect(mine) {
    if (stopped || mine !== ticket) return
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const currentSocket = new WebSocket(`${proto}//${window.location.host}/ws/metrics`)
    socket = currentSocket

    currentSocket.onopen = async () => {
      try {
        const token = await useAuthStore().ensureAccessToken()
        if (stopped || mine !== ticket || currentSocket.readyState !== WebSocket.OPEN) return
        if (!token) {
          currentSocket.close()
          return
        }
        // 令牌放在首条消息里，不放进连接地址。
        currentSocket.send(JSON.stringify({ token }))
      } catch {
        currentSocket.close()
      }
    }

    currentSocket.onmessage = (event) => {
      if (mine !== ticket) return
      try {
        push(JSON.parse(event.data))
        status.value = 'live'
        attempts = 0
        authRetries = 0
      } catch {
        // 忽略坏数据，保持连接。
      }
    }

    currentSocket.onclose = async (event) => {
      if (stopped || mine !== ticket) return
      status.value = 'reconnecting'
      if (event.code === 4401) {
        if (authRetries >= 1) {
          useAuthStore().clear()
          const { default: router } = await import('../router')
          await router.replace({ name: 'login' })
          return
        }
        authRetries += 1
        try {
          await useAuthStore().refresh()
        } catch {
          useAuthStore().clear()
          const { default: router } = await import('../router')
          await router.replace({ name: 'login' })
          return
        }
      }
      attempts += 1
      const delay = Math.min(10000, 1000 * 2 ** Math.min(attempts, 4))
      timer = window.setTimeout(() => connect(mine), delay)
    }
  }

  async function start() {
    stop()
    const mine = ticket
    stopped = false
    attempts = 0
    authRetries = 0
    status.value = 'connecting'
    try {
      const { data } = await http.get('/api/monitor/history')
      if (mine !== ticket) return
      history.value = Array.isArray(data.points) ? data.points.slice(-120) : []
    } catch {
      if (mine !== ticket) return
      history.value = []
    }
    connect(mine)
  }

  function stop() {
    ticket += 1
    stopped = true
    status.value = 'idle'
    window.clearTimeout(timer)
    if (socket) {
      const currentSocket = socket
      socket = null
      currentSocket.onclose = null
      currentSocket.close()
    }
  }

  return { history, current, status, start, stop }
})
