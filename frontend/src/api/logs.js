import { http, isCanceled } from './http'

function read(signal) {
  return { timeout: 8000, signal }
}

export function logOperations(params, signal) {
  return http.get('/api/logs/operations', { params, ...read(signal) })
}

export function logLogins(params, signal) {
  return http.get('/api/logs/logins', { params, ...read(signal) })
}

export function logAudit(params, signal) {
  return http.get('/api/logs/audit', { params, ...read(signal) })
}

export function logRuntime(params, signal) {
  return http.get('/api/logs/runtime', { params, ...read(signal) })
}

export function logTasks(params, signal) {
  return http.get('/api/logs/tasks', { params, ...read(signal) })
}

export function logSites(params, signal) {
  return http.get('/api/logs/sites', { params, ...read(signal) })
}

export function logSiteFiles(signal) {
  return http.get('/api/logs/site-files', { ...read(signal) })
}

export function logSsh(params, signal) {
  return http.get('/api/logs/ssh', { params, ...read(signal) })
}

export function logSoftware(params, signal) {
  return http.get('/api/logs/software', { params, ...read(signal) })
}

export function exportLogs(params) {
  return http.get('/api/logs/export', { params, responseType: 'blob', timeout: 20000 })
}

export function clearLogs(payload) {
  return http.post('/api/logs/clear', payload)
}

export function logError(error) {
  if (isCanceled(error)) return ''
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (error?.code === 'ECONNABORTED') return '读取超时，请重试'
  return '读取日志失败'
}
