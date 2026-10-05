let card = null
let timer = 0
let lastText = ""
let lastAt = 0

function ensureCard() {
  if (card) return card
  const host = document.createElement("div")
  host.setAttribute("role", "alert")
  host.style.cssText = "position:fixed;inset:0;z-index:80;display:none;align-items:center;justify-content:center;pointer-events:none;"
  card = document.createElement("div")
  card.style.cssText = "pointer-events:auto;max-width:28rem;margin:16px;padding:14px 18px;border:1px solid #e55353;background:#fff5f5;color:#c24141;border-radius:6px;font-size:14px;line-height:1.6;text-align:center;"
  card.addEventListener("click", () => {
    host.style.display = "none"
  })
  host.appendChild(card)
  document.body.appendChild(host)
  return card
}

export function showError(text) {
  const message = String(text || "操作失败").replace(/\s+/g, " ").trim().slice(0, 500) || "操作失败"
  const now = Date.now()
  if (message === lastText && now - lastAt < 800) return
  lastText = message
  lastAt = now
  const node = ensureCard()
  node.textContent = message
  node.parentElement.style.display = "flex"
  window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    if (node.parentElement) node.parentElement.style.display = "none"
  }, 5000)
}

export function friendlyMessage(text) {
  const message = String(text || "").replace(/\s+/g, " ").trim()
  if (!message) return "操作失败"
  const chinese = /[\u4e00-\u9fff]/.test(message)
  const raw = /nginx:|Traceback|\bemerg\b|open\(|No such file|address already in use/i.test(message)
  if (chinese && !raw) return message.slice(0, 180)
  if (/address already in use|bind\(/i.test(message)) return "该端口已被系统占用，请更换端口"
  if (/nginx:|\bemerg\b|open\(|No such file/i.test(message)) return "站点配置没有通过检查，原来的配置仍然保留"
  if (/Traceback|Error:/i.test(message)) return "操作失败，请稍后重试"
  return chinese ? message.slice(0, 180) : "操作失败，请稍后重试"
}

export function readErrorMessage(error) {
  const data = error?.response?.data
  if (data && typeof data.message === "string" && data.message) return friendlyMessage(data.message)
  const detail = data?.detail
  if (typeof detail === "string" && detail) return friendlyMessage(detail)
  if (detail && typeof detail.message === "string" && detail.message) return friendlyMessage(detail.message)
  if (data && data.status === "error" && typeof data.message === "string" && data.message) return friendlyMessage(data.message)
  if (!error?.response) return "操作失败"
  return "操作失败"
}

export function shouldToast(error) {
  const url = String(error?.config?.url || "")
  if (url.includes("/api/auth/")) return false
  if (url.includes("/api/sites/logs")) return false
  return true
}
