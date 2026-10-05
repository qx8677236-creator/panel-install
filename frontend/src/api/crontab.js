import { http } from './http'

export function listCrontab() {
  return http.get('/api/crontab', { cancelKey: 'cron:list' })
}

export function saveCrontab(payload) {
  return http.post('/api/crontab/save', payload)
}

export function deleteCrontab(id, confirm) {
  return http.post('/api/crontab/delete', { id, confirm })
}

export function toggleCrontab(id, enabled) {
  return http.post('/api/crontab/toggle', { id, enabled })
}

export function runCrontab(id) {
  return http.post('/api/crontab/run', { id })
}

export function crontabLogs(id) {
  return http.get('/api/crontab/logs', { params: { id } })
}
