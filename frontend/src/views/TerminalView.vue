<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { terminalConfig } from '../api/panel'
import { useAuthStore } from '../stores/auth'

const box = ref(null)
const notice = ref('')
let term
let socket
let fit

function socketUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/terminal`
}

onMounted(async () => {
  const auth = useAuthStore()
  const token = await auth.ensureAccessToken()
  const { data } = await terminalConfig()
  term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    theme: data.terminal_theme
      ? { background: '#1e1e1e', foreground: '#d4d4d4' }
      : { background: '#f7f8fa', foreground: '#1f2328' },
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(box.value)
  fit.fit()
  socket = new WebSocket(socketUrl())
  socket.binaryType = 'arraybuffer'
  socket.onopen = () => {
    socket.send(JSON.stringify({ token }))
  }
  socket.onmessage = (event) => {
    if (typeof event.data !== 'string') {
      term.write(new Uint8Array(event.data))
      return
    }
    try {
      const payload = JSON.parse(event.data)
      if (payload.type === 'ready') {
        notice.value = `已连接 ${payload.user}`
        socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
      }
    } catch (error) {
      term.write(event.data)
    }
  }
  socket.onclose = () => {
    notice.value = '终端已断开'
  }
  term.onData((data) => {
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'input', data }))
    }
  })
  window.addEventListener('resize', resize)
})

function resize() {
  if (!fit || !term || !socket || socket.readyState !== WebSocket.OPEN) return
  fit.fit()
  socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
}

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  socket?.close()
  term?.dispose()
})
</script>

<template>
  <div class="flex h-full min-h-0 flex-col p-3">
    <p class="mb-2 text-xs text-panel-muted">{{ notice || '正在连接终端' }}。这是面板用户的 bash，不会自动切换到 root。</p>
    <div ref="box" class="min-h-0 flex-1 overflow-hidden rounded border border-panel-line"></div>
  </div>
</template>
