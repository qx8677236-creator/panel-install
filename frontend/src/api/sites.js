import { http } from './http'

const write = { timeout: 20000 }

export function listSites() {
  return http.get('/api/sites')
}

export function siteDetail(domain) {
  return http.get('/api/sites/detail', { params: { domain } })
}

export function diagnoseSite(domain) {
  return http.get('/api/sites/diagnose', { params: { domain }, timeout: 15000 })
}

export function createSite(domain, root, kind = 'html') {
  return http.post('/api/sites/create', { domain, root, kind }, { ...write, cancelKey: 'site-create' })
}

export function deleteSite(domain, deleteFiles, deleteDatabase, confirm) {
  return http.post(
    '/api/sites/delete',
    {
      domain,
      delete_files: Boolean(deleteFiles),
      delete_database: Boolean(deleteDatabase),
      confirm,
    },
    { ...write, cancelKey: 'site-delete' },
  )
}

export function toggleSite(domain, enabled) {
  return http.post('/api/sites/toggle', { domain, enabled }, write)
}

export function saveRewrite(domain, preset, custom) {
  return http.post('/api/sites/rewrite', { domain, preset, custom }, write)
}

export function saveProxies(domain, proxies) {
  return http.post('/api/sites/proxy', { domain, proxies }, write)
}

export function proxyConfig(domain) {
  return http.get('/api/site/proxy-config', { params: { domain }, timeout: 8000 })
}

export function saveProxyRules(domain, rules) {
  return http.post('/api/site/save-proxy', { domain, rules }, write)
}

export function saveSsl(domain, enabled, forceHttps = true) {
  return http.post('/api/sites/ssl', { domain, enabled, force_https: forceHttps }, write)
}

export function issueCertificate(domain, email) {
  return http.post('/api/sites/ssl/issue', { domain, email }, { timeout: 180000 })
}

export function pasteCertificate(domain, certificate, key, forceHttps) {
  return http.post('/api/sites/ssl/paste', { domain, certificate, key, force_https: forceHttps }, write)
}

export function saveDomains(domain, domains) {
  return http.post('/api/sites/domains', { domain, domains }, write)
}

export function saveBindings(domain, bindings) {
  return http.post('/api/sites/bindings', { domain, bindings }, write)
}

export function saveAccess(domain, payload) {
  return http.post('/api/sites/access', { domain, ...payload }, write)
}

export function saveLimit(domain, conn, rate) {
  return http.post('/api/sites/limit', { domain, conn, rate }, write)
}

export function saveHotlink(domain, enabled, domains) {
  return http.post('/api/sites/hotlink', { domain, enabled, domains }, write)
}

export function saveRedirects(domain, redirects) {
  return http.post('/api/sites/redirects', { domain, redirects }, write)
}

export function saveConfig(domain, content) {
  return http.post('/api/sites/config', { domain, content }, write)
}

export function readLog(domain, kind, offset = 0, signal) {
  return http.get('/api/sites/logs', { params: { domain, kind, offset }, timeout: 8000, signal })
}

export function siteError(error) {
  if (
    error?.name === 'AbortError'
    || error?.code === 'ERR_CANCELED'
    || error?.cause?.name === 'AbortError'
  ) {
    return { message: '', log: '', canceled: true }
  }
  const detail = error?.response?.data?.detail
  if (Array.isArray(detail)) return { message: '提交的内容不符合要求', log: '' }
  if (detail && typeof detail === 'object') {
    return { message: detail.message || '操作失败', log: detail.log || '' }
  }
  if (typeof detail === 'string') return { message: detail, log: '' }
  return { message: '操作失败，请稍后重试', log: '' }
}
