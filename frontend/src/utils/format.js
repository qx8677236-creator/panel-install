export function formatBytes(value) {
  const size = Number(value)
  if (!Number.isFinite(size)) return '--'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let current = Math.max(0, size)
  let index = 0
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024
    index += 1
  }
  const digits = index === 0 ? 0 : current >= 100 ? 0 : current >= 10 ? 1 : 2
  return `${current.toFixed(digits)} ${units[index]}`
}

export function formatSpeed(bytesPerSecond) {
  return `${formatBytes(bytesPerSecond)}/s`
}

export function formatPercent(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  return `${number.toFixed(1)}%`
}

export function formatUptime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (days > 0) return `${days} 天 ${hours} 小时 ${minutes} 分钟`
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`
  return `${minutes} 分钟`
}

function beijingPart(date, type) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  return parts.find((part) => part.type === type)?.value || '00'
}

export function formatDateTime(timestamp) {
  if (!timestamp) return '--'
  const date = new Date(timestamp * 1000)
  if (Number.isNaN(date.getTime())) return '--'
  return `${beijingPart(date, 'year')}-${beijingPart(date, 'month')}-${beijingPart(date, 'day')} ${beijingPart(date, 'hour')}:${beijingPart(date, 'minute')}:${beijingPart(date, 'second')}`
}

export function formatClock(timestamp) {
  const date = new Date(timestamp * 1000)
  if (Number.isNaN(date.getTime())) return ''
  return `${beijingPart(date, 'hour')}:${beijingPart(date, 'minute')}:${beijingPart(date, 'second')}`
}

export function usageColor(percent, normal) {
  const value = Number(percent) || 0
  if (value >= 90) return '#e55353'
  if (value >= 80) return '#e6a23c'
  return normal
}
