<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import CodeEditor from './CodeEditor.vue'
import {
  issueCertificate,
  pasteCertificate,
  readLog,
  saveAccess,
  saveBindings,
  saveConfig,
  saveDomains,
  saveHotlink,
  saveLimit,
  proxyConfig,
  saveProxyRules,
  saveProxies,
  saveRedirects,
  saveRewrite,
  saveSsl,
  siteDetail,
  siteError,
} from '../api/sites'
import { showError } from '../utils/message'
import { acmeCerts, acmeDeploy, acmeOrders, acmeRecord, acmeRenew, saveSecurityHeaders } from '../api/security'

const props = defineProps({
  domain: { type: String, required: true },
  templates: { type: Object, default: () => ({}) },
  initialTab: { type: String, default: 'domain' },
})
const emit = defineEmits(['close', 'saved'])
const router = useRouter()

function openRoot() {
  const path = detail?.value?.root
  if (!path) return
  emit('close')
  router.push({ name: 'files', query: { path } })
}

const menus = [
  ['domain', '域名管理'],
  ['binding', '子目录绑定'],
  ['webroot', '网站目录'],
  ['access', '访问限制'],
  ['limit', '流量限制'],
  ['rewrite', '伪静态'],
  ['index', '默认文档'],
  ['config', '配置文件'],
  ['ssl', 'SSL'],
  ['php', 'PHP'],
  ['redirect', '重定向'],
  ['proxy', '反向代理'],
  ['hotlink', '防盗链'],
  ['guard', '防篡改'],
  ['security', '网站安全'],
  ['logs', '网站日志'],
  ['alarm', '网站告警'],
  ['other', '其他设置'],
]

const fallbackTemplates = {
  none: 'try_files $uri $uri/ /index.html;',
  wordpress: 'try_files $uri $uri/ /index.php?$args;',
  laravel: 'try_files $uri $uri/ /index.php?$query_string;',
  thinkphp: 'try_files $uri $uri/ /index.php?s=$uri&$args;',
}

const tab = ref('domain')
const sslTab = ref('issue')
const busy = ref(false)
const loading = ref(false)
let loadSeq = 0
const message = ref('')
const errorLog = ref('')
const detail = ref(null)
const domains = ref([])
const domainInput = ref('')
const picked = ref([])
const batchAction = ref('')
const httpsPortText = computed(() => (detail.value?.ssl ? '已开启' : '未开启'))
const bindings = ref([])
const bindingDomain = ref('')
const bindingDir = ref('')
const preset = ref('none')
const rewriteText = ref('')
const proxies = ref([])
const proxyLoading = ref(false)
const email = ref('')
const certificate = ref('')
const privateKey = ref('')
const forceHttps = ref(true)
const authEnabled = ref(false)
const orders = ref([])
const securityForm = ref({
  enabled: false,
  x_frame_options: '',
  nosniff: false,
  xss: false,
  referrer: '',
  hsts: false,
})

function applySecurity(data) {
  const item = data?.security_headers || {}
  securityForm.value = {
    enabled: Boolean(item.enabled),
    x_frame_options: item.x_frame_options || '',
    nosniff: Boolean(item.nosniff),
    xss: Boolean(item.xss),
    referrer: item.referrer || '',
    hsts: Boolean(item.hsts),
  }
}

function recordAcme() {
  return run(() => acmeRecord(props.domain, email.value.trim()), '申请已记录，尚未向证书机构发起请求')
}

async function deployAcme() {
  const { data } = await acmeCerts()
  const found = (data.items || []).find((item) => item.ssl_hash === props.domain || item.siteName === props.domain)
  if (!found) {
    message.value = '证书夹里还没有这个站点的证书'
    return
  }
  return run(() => acmeDeploy(props.domain, found.ssl_hash), '已部署证书夹中的证书')
}
const realm = ref('Restricted')
const users = ref([])
const userForm = ref({ name: '', password: '' })
const conn = ref(0)
const rate = ref(0)
const hotlinkEnabled = ref(false)
const hotlinkText = ref('')
const redirects = ref([])
const redirectForm = ref({ path: '/', target: '', code: 301 })
const configText = ref('')
const editorRef = ref(null)
const logKind = ref('access')
const logText = ref('')
const logOffset = ref(0)
const logLoading = ref(false)
let timer = null
let logAbort = null

function fail(error) {
  const parsed = siteError(error)
  message.value = parsed.message
  errorLog.value = parsed.log || ''
}

function templateText(id) {
  return props.templates[id] || fallbackTemplates[id] || ''
}

async function load(resetNotice = false) {
  const seq = ++loadSeq
  loading.value = true
  if (resetNotice) {
    message.value = ''
    errorLog.value = ''
  }
  try {
    const { data } = await siteDetail(props.domain)
    if (seq !== loadSeq) return
    detail.value = data
    domains.value = (data.domains || []).map((item) => {
      if (item && typeof item === 'object') return { domain: item.domain, port: String(item.port ?? "80") }
      const parsed = parseDomainLine(String(item))
      return parsed || { domain: String(item), port: 80 }
    })
    picked.value = []
    bindings.value = (data.bindings || []).map((item) => ({ ...item }))
    preset.value = data.preset || ''
    rewriteText.value = data.rewrite_body || ''
    try {
      const proxyResult = await proxyConfig(props.domain)
      applyProxyRules(proxyResult.data.rules)
    } catch (error) {
      applyProxyRules(data.proxies)
    }
    forceHttps.value = Boolean(data.force_https)
    authEnabled.value = Boolean(data.auth?.enabled)
    realm.value = data.auth?.realm || ''
    users.value = (data.auth?.users || []).map((name) => ({ name, password: '', existing: true }))
    conn.value = Number(data.limit?.conn || 0)
    rate.value = Number(data.limit?.rate || 0)
    hotlinkEnabled.value = Boolean(data.hotlink?.enabled)
    hotlinkText.value = (data.hotlink?.domains || []).join('\n')
    applySecurity(data)
    try {
      orders.value = (await acmeOrders(props.domain)).data.items || []
    } catch (error) {
      orders.value = []
    }
    redirects.value = (data.redirects || []).map((item) => ({ ...item }))
    configText.value = data.config || ''
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function run(action, success) {
  busy.value = true
  message.value = ''
  errorLog.value = ''
  try {
    await action()
    message.value = success
    emit('saved')
    await load()
  } catch (error) {
    fail(error)
    showError(message.value)
  } finally {
    busy.value = false
  }
}

function parseDomainLine(line) {
  let name = String(line || '').trim().toLowerCase().replace(/\.+$/, '')
  let port = "80"
  const matched = name.match(/^(.*):(\d{1,5})$/)
  if (matched) {
    name = matched[1].replace(/\.+$/, '')
    port = matched[2]
  }
  if (!name) return null
  return { domain: name, port }
}

function domainKey(item) {
  return `${item.domain}:${item.port}`
}

async function addDomain() {
  const lines = domainInput.value.split(/\n+/).map((item) => item.trim()).filter(Boolean)
  if (!lines.length) return
  const next = domains.value.map((item) => ({ ...item }))
  lines.forEach((line) => {
    const parsed = parseDomainLine(line)
    if (!parsed) return
    if (next.some((item) => item.domain === parsed.domain && item.port === parsed.port)) return
    next.push(parsed)
  })
  await run(() => saveDomains(props.domain, next), '域名已添加')
  if (message.value === '域名已添加') domainInput.value = ''
}

async function removeDomains(extra = []) {
  const doomed = new Set([...picked.value, ...extra])
  const next = domains.value.filter((item) => item.domain === props.domain || !doomed.has(domainKey(item)))
  if (next.length === domains.value.length) {
    message.value = '主域名不能删除'
    errorLog.value = ''
    return
  }
  picked.value = []
  batchAction.value = ''
  await run(() => saveDomains(props.domain, next), '域名已删除')
}

function runBatch() {
  if (batchAction.value === 'delete') removeDomains()
}

function addBinding() {
  if (!bindingDomain.value.trim() || !bindingDir.value.trim()) return
  bindings.value.push({ domain: bindingDomain.value.trim().toLowerCase(), subdir: bindingDir.value.trim() })
  bindingDomain.value = ''
  bindingDir.value = ''
}

function useTemplate(id) {
  preset.value = id
  rewriteText.value = templateText(id)
}

function applyProxyRules(rules) {
  proxies.value = (rules || []).map((item) => ({
    name: item.name || '',
    path: item.path || '/',
    target_url: item.target_url || item.upstream || '',
    forward_ip: item.forward_ip !== false,
  }))
}

async function refreshProxy() {
  proxyLoading.value = true
  try {
    const { data } = await proxyConfig(props.domain)
    applyProxyRules(data.rules)
  } catch (error) {
    fail(error)
  } finally {
    proxyLoading.value = false
  }
}

function addProxy() {
  proxies.value.push({ name: '', path: '/', target_url: '', forward_ip: true })
}

function addUser() {
  if (!userForm.value.name.trim() || !userForm.value.password) return
  users.value.push({ name: userForm.value.name.trim(), password: userForm.value.password, existing: false })
  userForm.value = { name: '', password: '' }
}

function addRedirect() {
  redirects.value.push({ ...redirectForm.value, code: Number(redirectForm.value.code) })
}

function stopPoll() {
  if (timer) clearInterval(timer)
  timer = null
  logAbort?.abort()
}

async function pullLog(reset) {
  logAbort?.abort()
  const controller = new AbortController()
  logAbort = controller
  logLoading.value = true
  try {
    const offset = reset ? 0 : logOffset.value
    const { data } = await readLog(props.domain, logKind.value, offset, controller.signal)
    if (controller.signal.aborted) return
    if (reset || data.offset < logOffset.value) logText.value = data.text || ''
    else if (data.text) logText.value += data.text
    logOffset.value = data.offset || 0
  } catch (error) {
    if (controller.signal.aborted || error?.name === 'AbortError' || error?.code === 'ERR_CANCELED' || siteError(error).canceled) return
    fail(error)
    stopPoll()
  } finally {
    if (logAbort === controller) logLoading.value = false
  }
}

watch(tab, (value) => {
  stopPoll()
  load(false).catch(fail)
  if (value === 'logs') {
    logText.value = ''
    logOffset.value = 0
    pullLog(true)
    timer = setInterval(() => pullLog(false), 2000)
  }
})

watch(logKind, () => {
  if (tab.value !== 'logs') return
  logText.value = ''
  logOffset.value = 0
  pullLog(true)
})

watch(() => props.domain, () => {
  const next = props.initialTab || 'domain'
  if (tab.value !== next) tab.value = next
  else load(true).catch(fail)
}, { immediate: true })

onBeforeUnmount(stopPoll)
</script>

<template>
  <div class="fixed inset-0 z-20 flex items-center justify-center bg-black/30 p-4">
    <div class="flex h-[82vh] w-full max-w-5xl overflow-hidden rounded bg-white shadow-card">
      <aside class="w-44 shrink-0 overflow-auto border-r border-panel-line bg-[#f6f7f9] py-3">
        <button
          v-for="[id, label] in menus"
          :key="id"
          type="button"
          class="block w-full px-4 py-2.5 text-left text-sm disabled:opacity-50"
          :class="tab === id ? 'bg-white font-medium text-panel-green' : 'text-panel-text hover:bg-white/70'"
          :disabled="loading || busy"
          @click="tab = id"
        >
          {{ label }}
        </button>
      </aside>
      <section class="flex min-w-0 flex-1 flex-col">
        <div class="flex items-center justify-between border-b border-panel-line px-5 py-3">
          <h2 class="text-base font-medium">站点修改[{{ domain }}]</h2>
          <button type="button" class="tool" @click="emit('close')">关闭</button>
        </div>
        <div class="relative min-h-0 flex-1 overflow-auto px-5 py-4 text-sm">
          <div v-if="loading" class="absolute inset-0 z-10 flex items-center justify-center bg-white/90 text-sm text-panel-muted">正在读取站点配置</div>
          <div v-if="message" class="mb-3 rounded border border-panel-line bg-[#f7f8fa] px-3 py-2">
            <div>{{ message }}</div>
            <pre v-if="errorLog" class="log mt-2">{{ errorLog }}</pre>
          </div>

          <div v-if="tab === 'domain'" class="space-y-3">
            <div class="flex items-start gap-4">
              <textarea
                v-model="domainInput"
                class="field h-28"
                placeholder="如需绑定外网，请成行填写，每行一个域名，默认为80端口&#10;IP地址格式：192.168.1.199&#10;泛解析添加方法 *.domain.com&#10;如另加端口格式为 www.domain.com:88"
              ></textarea>
              <button type="button" class="primary h-10 px-6" :disabled="busy" @click="addDomain">添加</button>
            </div>
            <div class="text-right text-panel-muted">HTTPS端口：{{ httpsPortText }}</div>
            <table class="w-full text-left">
              <thead class="bg-[#f7f8fa] text-xs text-panel-muted">
                <tr>
                  <th class="w-10 px-3 py-2"></th>
                  <th class="px-3 py-2 font-medium">域名</th>
                  <th class="px-3 py-2 font-medium">端口</th>
                  <th class="px-3 py-2 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in domains" :key="domainKey(item)" class="border-t border-panel-line">
                  <td class="px-3 py-3">
                    <input v-model="picked" type="checkbox" :value="domainKey(item)" />
                  </td>
                  <td class="px-3 py-3 text-panel-green">{{ item.domain }}</td>
                  <td class="px-3 py-3">{{ item.port }}</td>
                  <td class="px-3 py-3 text-right">
                    <button v-if="item.domain !== domain" type="button" class="text-panel-green" :disabled="busy" @click="removeDomains([domainKey(item)])">删除</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div class="flex items-center gap-2">
              <select v-model="batchAction" class="field w-40">
                <option value="">请选择批量操作</option>
                <option value="delete">删除</option>
              </select>
              <button type="button" class="primary" :disabled="busy || !picked.length || !batchAction" @click="runBatch">批量操作</button>
            </div>
          </div>

          <div v-else-if="tab === 'binding'" class="space-y-3">
            <div class="grid grid-cols-[1fr_1fr_auto] gap-2">
              <input v-model="bindingDomain" class="field" placeholder="绑定域名" />
              <input v-model="bindingDir" class="field" placeholder="子目录，例如 api" />
              <button type="button" class="tool" @click="addBinding">添加</button>
            </div>
            <div v-for="(item, index) in bindings" :key="`${item.domain}-${index}`" class="flex items-center justify-between border-t border-panel-line py-2">
              <span>{{ item.domain }} → {{ item.subdir }}</span>
              <button type="button" class="tool" @click="bindings.splice(index, 1)">删除</button>
            </div>
            <button type="button" class="primary" :disabled="busy" @click="run(() => saveBindings(domain, bindings), '子目录绑定已保存')">保存</button>
          </div>

          <div v-else-if="tab === 'rewrite'" class="space-y-3">
            <div class="flex flex-wrap gap-2">
              <button v-for="id in ['none', 'wordpress', 'laravel', 'thinkphp']" :key="id" type="button" class="tool" @click="useTemplate(id)">
                {{ { none: '默认', wordpress: 'WordPress', laravel: 'Laravel', thinkphp: 'ThinkPHP' }[id] }}
              </button>
            </div>
            <textarea v-model="rewriteText" class="field h-48 font-mono text-xs" @input="preset = 'custom'"></textarea>
            <button type="button" class="primary" :disabled="busy" @click="run(() => saveRewrite(domain, preset, rewriteText), '伪静态已保存')">保存</button>
          </div>

          <div v-else-if="tab === 'proxy'" class="space-y-3">
            <p v-if="proxyLoading" class="text-sm text-panel-muted">正在读取反向代理配置</p>
            <p v-else-if="!proxies.length" class="text-sm text-panel-muted">还没有反向代理规则</p>
            <div v-for="(item, index) in proxies" :key="index" class="space-y-2 border-t border-panel-line pt-3">
              <div class="grid grid-cols-3 gap-2">
                <input v-model="item.name" class="field" placeholder="代理名称" />
                <input v-model="item.path" class="field" placeholder="路径，例如 / 或 /api/" />
                <input v-model="item.target_url" class="field" placeholder="目标 URL，例如 http://10.0.0.5:9000" />
              </div>
              <div class="flex items-center justify-between">
                <label class="flex items-center gap-2 text-sm">
                  <input v-model="item.forward_ip" type="checkbox" />
                  携带客户端真实 IP
                </label>
                <button type="button" class="tool" @click="proxies.splice(index, 1)">删除</button>
              </div>
            </div>
            <button type="button" class="tool" @click="addProxy">添加规则</button>
            <p class="text-xs text-panel-muted">保存后写入对应 location 的 proxy_pass。路径为 / 时会接管网站根路径。</p>
            <button type="button" class="primary" :disabled="busy" @click="run(async () => { const { data } = await saveProxyRules(domain, proxies); applyProxyRules(data.rules) }, '反向代理已保存')">保存</button>
          </div>

          <div v-else-if="tab === 'ssl'" class="space-y-3">
            <div class="flex gap-2">
              <button type="button" class="tool" :class="{ 'text-panel-green': sslTab === 'issue' }" @click="sslTab = 'issue'">一键申请</button>
              <button type="button" class="tool" :class="{ 'text-panel-green': sslTab === 'paste' }" @click="sslTab = 'paste'">证书夹</button>
            </div>
            <label class="flex items-center gap-2">
              <input v-model="forceHttps" type="checkbox" />
              强制 HTTPS（80 端口 301 跳转）
            </label>
            <div v-if="sslTab === 'issue'" class="space-y-2">
              <p class="text-xs text-panel-muted">申请会调用 Certbot 的网站目录验证，并表示同意 Let's Encrypt 服务条款。域名需要已经解析到这台机器。</p>
              <input v-model="email" class="field" placeholder="邮箱" />
              <button type="button" class="primary" :disabled="busy" @click="run(() => issueCertificate(domain, email.trim()), '证书申请已提交')">申请证书</button>
              <button type="button" class="tool" :disabled="busy" @click="recordAcme">只记录申请</button>
              <div v-for="order in orders" :key="order.index" class="flex items-center justify-between gap-2 text-xs">
                <span>#{{ order.index }} {{ order.message }}</span>
                <button type="button" class="tool" @click="run(() => acmeRenew(order.index), '续签已记录，尚未向证书机构发起请求')">记录续签</button>
              </div>
            </div>
            <div v-else class="space-y-2">
              <textarea v-model="certificate" class="field h-28 font-mono text-xs" placeholder="粘贴 fullchain.pem"></textarea>
              <textarea v-model="privateKey" class="field h-28 font-mono text-xs" placeholder="粘贴 privkey.pem"></textarea>
              <button type="button" class="primary" :disabled="busy" @click="run(() => pasteCertificate(domain, certificate, privateKey, forceHttps), '证书已保存')">保存证书</button>
              <button type="button" class="tool" :disabled="busy" @click="deployAcme">部署已有证书</button>
            </div>
            <div class="text-xs text-panel-muted">当前状态：{{ detail?.ssl ? '已启用 HTTPS' : '未启用' }}，到期：{{ detail?.expires_at || '未配置' }}</div>
            <button type="button" class="tool" :disabled="busy" @click="run(() => saveSsl(domain, !detail?.ssl, forceHttps), detail?.ssl ? '已关闭 HTTPS' : '已开启 HTTPS')">
              {{ detail?.ssl ? '关闭 HTTPS' : '开启 HTTPS' }}
            </button>
          </div>

          <div v-else-if="tab === 'access'" class="space-y-3">
            <label class="flex items-center gap-2"><input v-model="authEnabled" type="checkbox" /> 开启目录密码保护</label>
            <input v-model="realm" class="field" placeholder="认证提示" />
            <div class="grid grid-cols-[1fr_1fr_auto] gap-2">
              <input v-model="userForm.name" class="field" placeholder="用户名" />
              <input v-model="userForm.password" class="field" type="password" placeholder="密码" />
              <button type="button" class="tool" @click="addUser">添加</button>
            </div>
            <div v-for="(item, index) in users" :key="item.name" class="flex items-center justify-between border-t border-panel-line py-2">
              <span>{{ item.name }}</span>
              <button type="button" class="tool" @click="users.splice(index, 1)">删除</button>
            </div>
            <button type="button" class="primary" :disabled="busy" @click="run(() => saveAccess(domain, { enabled: authEnabled, realm, users }), '访问限制已保存')">保存</button>
          </div>

          <div v-else-if="tab === 'limit'" class="space-y-3">
            <label class="block text-xs text-panel-muted">单个 IP 并发连接数，0 表示不限制</label>
            <input v-model.number="conn" class="field" type="number" min="0" max="500" />
            <label class="block text-xs text-panel-muted">单个 IP 每秒请求数，0 表示不限制</label>
            <input v-model.number="rate" class="field" type="number" min="0" max="500" />
            <button type="button" class="primary" :disabled="busy" @click="run(() => saveLimit(domain, Number(conn) || 0, Number(rate) || 0), '流量限制已保存')">保存</button>
          </div>

          <div v-else-if="tab === 'hotlink'" class="space-y-3">
            <label class="flex items-center gap-2"><input v-model="hotlinkEnabled" type="checkbox" /> 开启防盗链，不匹配的请求返回 403</label>
            <textarea v-model="hotlinkText" class="field h-32" placeholder="每行一个允许的域名"></textarea>
            <button
              type="button"
              class="primary"
              :disabled="busy"
              @click="run(() => saveHotlink(domain, hotlinkEnabled, hotlinkText.split(/\n+/).map((item) => item.trim()).filter(Boolean)), '防盗链已保存')"
            >
              保存
            </button>
          </div>

          <div v-else-if="tab === 'redirect'" class="space-y-3">
            <div class="grid grid-cols-[1fr_1.4fr_90px_auto] gap-2">
              <input v-model="redirectForm.path" class="field" placeholder="来源路径 /" />
              <input v-model="redirectForm.target" class="field" placeholder="目标 URL" />
              <select v-model="redirectForm.code" class="field">
                <option :value="301">301</option>
                <option :value="302">302</option>
              </select>
              <button type="button" class="tool" @click="addRedirect">添加</button>
            </div>
            <div v-for="(item, index) in redirects" :key="index" class="flex items-center justify-between border-t border-panel-line py-2">
              <span>{{ item.code }} {{ item.path }} → {{ item.target }}</span>
              <button type="button" class="tool" @click="redirects.splice(index, 1)">删除</button>
            </div>
            <button type="button" class="primary" :disabled="busy" @click="run(() => saveRedirects(domain, redirects), '重定向已保存')">保存</button>
          </div>

          <div v-else-if="tab === 'config'" class="space-y-3">
            <p class="text-xs text-panel-muted">这里是该站点的完整配置。保存前会先做语法检查，通过后才覆盖正式文件。</p>
            <div class="h-96 overflow-hidden rounded border border-panel-line">
              <CodeEditor ref="editorRef" filename="nginx.conf" :initial="configText" />
            </div>
            <button type="button" class="primary" :disabled="busy" @click="run(() => saveConfig(domain, editorRef?.getText() || configText), '配置已保存')">保存配置</button>
          </div>

          <div v-else-if="tab === 'webroot'" class="space-y-2">
            <div class="text-xs text-panel-muted">网站目录</div>
            <button type="button" class="rounded border border-panel-line bg-[#f7f8fa] px-3 py-2 text-left font-mono text-xs text-panel-green hover:underline" @click="openRoot">{{ detail?.root || '未设置' }}</button>
            <p class="text-xs text-panel-muted">点击目录可进入文件管理。目录位于文件根目录内，不会改到其他服务。</p>
          </div>

          <div v-else-if="tab === 'index'" class="space-y-2">
            <div class="text-xs text-panel-muted">默认文档</div>
            <div v-if="detail?.index_files" class="rounded border border-panel-line px-3 py-2">{{ detail.index_files }}</div>
            <p v-else class="text-xs text-panel-muted">配置里没有 index 指令。</p>
            <p class="text-xs text-panel-muted">这里显示配置文件中的 index 指令，不会另外补一套默认文件名。</p>
          </div>

          <div v-else-if="tab === 'php'" class="space-y-2">
            <div class="text-xs text-panel-muted">PHP</div>
            <p>这台服务器没有为站点接入 PHP。保存配置时不会写入 PHP 转发，也不会改已有服务的端口。</p>
          </div>

          <div v-else-if="tab === 'guard'" class="space-y-2">
            <div class="text-xs text-panel-muted">防篡改</div>
            <p>当前没有开启文件监控。网站目录仍受文件根目录限制，不能写到目录外面。</p>
          </div>

          <div v-else-if="tab === 'security'" class="space-y-3">
            <label class="flex items-center gap-2 text-sm"><input v-model="securityForm.enabled" type="checkbox" /> 开启安全响应头</label>
            <div class="grid gap-2 md:grid-cols-2">
              <label class="text-xs text-panel-muted">X-Frame-Options
                <select v-model="securityForm.x_frame_options" class="field">
                  <option value="">不设置</option>
                  <option value="SAMEORIGIN">SAMEORIGIN</option>
                  <option value="DENY">DENY</option>
                </select>
              </label>
              <label class="text-xs text-panel-muted">Referrer-Policy
                <select v-model="securityForm.referrer" class="field">
                  <option value="">不设置</option>
                  <option value="strict-origin-when-cross-origin">strict-origin-when-cross-origin</option>
                  <option value="same-origin">same-origin</option>
                  <option value="no-referrer">no-referrer</option>
                </select>
              </label>
            </div>
            <label class="flex items-center gap-2 text-sm"><input v-model="securityForm.nosniff" type="checkbox" /> X-Content-Type-Options: nosniff</label>
            <label class="flex items-center gap-2 text-sm"><input v-model="securityForm.xss" type="checkbox" /> X-XSS-Protection</label>
            <label class="flex items-center gap-2 text-sm"><input v-model="securityForm.hsts" type="checkbox" /> HSTS（只在已启用 HTTPS 时写入）</label>
            <button type="button" class="primary" :disabled="busy" @click="run(() => saveSecurityHeaders(domain, securityForm), '安全响应头已保存')">保存</button>
          </div>

          <div v-else-if="tab === 'alarm'" class="space-y-2">
            <div class="text-xs text-panel-muted">网站告警</div>
            <p>当前没有告警记录。</p>
          </div>

          <div v-else-if="tab === 'other'" class="space-y-2">
            <div>运行状态：{{ detail?.enabled ? '运行中' : '已停止' }}</div>
            <button type="button" class="text-panel-green hover:underline" @click="openRoot">网站目录：{{ detail?.root || '未设置' }}</button>
            <div>HTTPS：{{ httpsPortText }}</div>
          </div>

          <div v-else-if="tab === 'logs'" class="space-y-3">
            <div class="flex gap-2">
              <button type="button" class="tool" :class="{ 'text-panel-green': logKind === 'access' }" @click="logKind = 'access'">访问日志</button>
              <button type="button" class="tool" :class="{ 'text-panel-green': logKind === 'error' }" @click="logKind = 'error'">错误日志</button>
              <span v-if="logLoading" class="self-center text-xs text-panel-muted">正在读取</span>
            </div>
            <pre class="log h-96">{{ logText || '还没有日志' }}</pre>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tool {
  border: 1px solid #e7e9ed;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  color: #2b2f36;
}
.tool:disabled { opacity: 0.45; }
.tool:not(:disabled):hover { border-color: #20a53a; color: #20a53a; }
.primary {
  border-radius: 4px;
  background: #20a53a;
  padding: 4px 12px;
  font-size: 12px;
  color: white;
}
.primary:disabled { opacity: 0.45; }
.field {
  width: 100%;
  border: 1px solid #e7e9ed;
  border-radius: 4px;
  padding: 6px 8px;
  outline: none;
}
.field:focus { border-color: #20a53a; }
.log {
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  border-radius: 4px;
  background: #1f2a38;
  padding: 8px;
  font-size: 12px;
  color: #d7dde6;
}
.h-96.log { max-height: none; }
</style>
