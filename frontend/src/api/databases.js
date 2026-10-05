import { http, isCanceled } from './http'

export function databaseStatus(engine = '') {
  return http.get('/api/database/status', { params: engine ? { engine } : {}, timeout: 8000, cancelKey: 'db:status' })
}

export function rootPasswordOk(value) {
  if (typeof value !== 'string' || value.length < 8 || value.length > 64) return false
  if (value === 'Admin123!@#') return false
  if (/['";\s\u0000]/.test(value)) return false
  return /[A-Z]/.test(value) && /[a-z]/.test(value) && /\d/.test(value) && /[^A-Za-z0-9]/.test(value)
}

export function installEngine(engine, password = '') {
  return http.post('/api/database/install', { engine, root_password: password }, { timeout: 15000 })
}

export function installProgress(engine) {
  return http.get('/api/database/progress', { params: { engine }, timeout: 8000 })
}

export function startEngine(engine) {
  return http.post('/api/database/start', { engine }, { timeout: 40000 })
}

export function stopEngine(engine) {
  return http.post('/api/database/stop', { engine }, { timeout: 40000 })
}

export function listDatabases(page = 1) {
  return http.get('/api/databases', { params: { page, page_size: 50 }, timeout: 8000, cancelKey: 'db:panel' })
}

export function saveRemote(payload) {
  return http.post('/api/databases/remote', payload, { timeout: 8000 })
}

export function createDatabase(payload) {
  return http.post('/api/databases/create', payload)
}

export function deleteDatabase(name, confirm) {
  return http.post('/api/databases/delete', { name, confirm })
}

export function changeDatabasePassword(name, password) {
  return http.post('/api/databases/password', { name, password })
}

export function changeDatabaseAccess(payload) {
  return http.post('/api/databases/access', payload)
}

export function changeRootPassword(password) {
  return http.post('/api/databases/root-password', { password })
}

export function showRootPassword() {
  return http.post('/api/databases/root-password/show', {}, { timeout: 8000 })
}

export function backupDatabase(name) {
  return http.post('/api/databases/backup', { name })
}

export function backupJob(jobId) {
  return http.get('/api/databases/backup/status', { params: { job_id: jobId }, timeout: 8000 })
}

export function downloadBackup(name, file) {
  return http.get('/api/databases/backup/download', {
    params: { name, file },
    responseType: 'blob',
    timeout: 120000,
  })
}

export function listBackups(name) {
  return http.get('/api/databases/backups', { params: { name } })
}

export function restoreDatabase(name, file) {
  return http.post('/api/databases/restore', { name, file }, { timeout: 120000 })
}

export function sqlserverList() {
  return http.get('/api/database/sqlserver', { timeout: 20000, cancelKey: 'db:panel' })
}

export function sqlserverCreate(name) {
  return http.post('/api/database/sqlserver/create', { name })
}

export function sqlserverDelete(name, confirm) {
  return http.post('/api/database/sqlserver/delete', { name, confirm })
}

export function sqlserverBackup(name) {
  return http.post('/api/database/sqlserver/backup', { name }, { timeout: 180000 })
}

export function sqlserverBackups(name) {
  return http.get('/api/database/sqlserver/backups', { params: { name } })
}

export function sqlserverRestore(name, file) {
  return http.post('/api/database/sqlserver/restore', { name, file }, { timeout: 180000 })
}

export function mongoList() {
  return http.get('/api/database/mongodb', { timeout: 8000, cancelKey: 'db:panel' })
}

export function mongoCreate(payload) {
  return http.post('/api/database/mongodb/create', payload)
}

export function mongoDelete(name, confirm) {
  return http.post('/api/database/mongodb/delete', { name, confirm })
}

export function redisInfo() {
  return http.get('/api/database/redis', { timeout: 8000, cancelKey: 'db:panel' })
}

export function redisFlush(confirm) {
  return http.post('/api/database/redis/flush', { confirm })
}

export function redisPassword(password) {
  return http.post('/api/database/redis/password', { password })
}

export function postgresList() {
  return http.get('/api/database/pgsql', { timeout: 8000, cancelKey: 'db:panel' })
}

export function postgresCreate(payload) {
  return http.post('/api/database/pgsql/create', payload)
}

export function postgresDelete(name, confirm) {
  return http.post('/api/database/pgsql/delete', { name, confirm })
}

export function postgresPassword(user, password) {
  return http.post('/api/database/pgsql/password', { user, password })
}

export function postgresDeleteRole(name, confirm) {
  return http.post('/api/database/pgsql/delete-role', { name, confirm })
}

export function sqliteFiles() {
  return http.get('/api/database/sqlite/files', { timeout: 8000, cancelKey: 'db:panel' })
}

export function sqliteTables(file) {
  return http.get('/api/database/sqlite/tables', { params: { file }, timeout: 8000 })
}

export function sqliteRows(file, table, page = 1) {
  return http.get('/api/database/sqlite/rows', { params: { file, table, page }, timeout: 8000 })
}

export function databaseError(error) {
  if (isCanceled(error)) return ''
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (error?.code === 'ECONNABORTED') return '操作超时，请重试'
  return '操作失败，请稍后重试'
}

export function mysqlUsers() {
  return http.get('/api/database/mysql/users')
}

export function mysqlAddUser(payload) {
  return http.post('/api/database/mysql/users', payload)
}

export function mysqlDeleteUser(username, host) {
  return http.post('/api/database/mysql/users/delete', { username, host })
}

export function redisConfig() {
  return http.get('/api/database/redis/config')
}

export function redisSaveConfig(maxmemory, policy) {
  return http.post('/api/database/redis/config', { maxmemory, policy })
}

export function mongoUsers() {
  return http.get('/api/database/mongodb/users')
}

export function mongoAddUser(payload) {
  return http.post('/api/database/mongodb/users', payload)
}

export function mongoDeleteUser(username, db_name) {
  return http.post('/api/database/mongodb/users/delete', { username, db_name })
}
