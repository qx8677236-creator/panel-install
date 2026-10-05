import { http } from './http'

export function firewallStatus() {
  return http.get('/api/firewall', { timeout: 8000, cancelKey: 'security:firewall' })
}

export function warningScan(domain = '') {
  return http.get('/api/warning', { params: { domain } })
}

export function sshConfig() {
  return http.get('/api/ssh_security/config', { cancelKey: 'security:ssh' })
}

export function sshScan() {
  return http.post('/api/ssh_security/scan', {})
}

export function securityHeaders(domain) {
  return http.get('/api/sites/security-headers', { params: { domain } })
}

export function saveSecurityHeaders(domain, security) {
  return http.post('/api/sites/security-headers', { domain, ...security })
}

export function acmeOrders(domain) {
  return http.get('/api/sites/acme/orders', { params: { domain } })
}

export function acmeRecord(domain, email) {
  return http.post('/api/sites/acme/record', { domain, email })
}

export function acmeRenew(index) {
  return http.post('/api/sites/acme/renew', { index })
}

export function acmeCerts() {
  return http.get('/api/sites/acme/certs')
}

export function acmeDeploy(domain, ssl_hash) {
  return http.post('/api/sites/acme/deploy', { domain, ssl_hash })
}
