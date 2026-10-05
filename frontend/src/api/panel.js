import { ref } from 'vue'
import { http } from './http'

export const panelTitle = ref('删库跑路快捷助手')

export function panelSettings() {
  return http.get('/api/panel/settings')
}

export function savePanelTitle(title) {
  return http.post('/api/panel/title', { title })
}

export function changePanelPassword(old_password, new_password) {
  return http.post('/api/panel/password', { old_password, new_password })
}

export function terminalConfig() {
  return http.get('/api/xterm/config')
}

export function saveTerminalConfig(payload) {
  return http.post('/api/xterm/config', payload)
}

export function panelError(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return '操作失败，请稍后重试'
}
