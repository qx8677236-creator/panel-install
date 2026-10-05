import { http } from './http'

export function listBackups() {
  return http.get('/api/backup', { cancelKey: 'backup:list' })
}

export function backupOptions() {
  return http.get('/api/backup/options', { timeout: 20000, cancelKey: 'backup:options' })
}

export function saveBackup(payload) {
  return http.post('/api/backup/save', payload)
}

export function deleteBackup(id, confirm) {
  return http.post('/api/backup/delete', { id, confirm })
}

export function toggleBackup(id, enabled) {
  return http.post('/api/backup/toggle', { id, enabled })
}

export function runBackup(id) {
  return http.post('/api/backup/run', { id })
}

export function backupLogs(id) {
  return http.get('/api/backup/logs', { params: { id }, timeout: 20000 })
}
