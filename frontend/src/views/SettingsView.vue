<script setup>
import { onMounted, ref } from 'vue'
import { changePanelPassword, panelError, panelSettings, panelTitle, saveAllowIps, savePanelTitle, saveTerminalConfig } from '../api/panel'

const form = ref(null)
const title = ref('')
const allowIps = ref('')
const oldPassword = ref('')
const newPassword = ref('')
const message = ref('')
const messageBad = ref(false)

async function load() {
  const { data } = await panelSettings()
  form.value = data
  title.value = data.title || ''
  allowIps.value = data.allow_ips || ''
  panelTitle.value = title.value || '删库跑路快捷助手'
}

function fail(error) {
  messageBad.value = true
  message.value = panelError(error)
}

async function saveTitle() {
  message.value = ''
  messageBad.value = false
  try {
    form.value = (await savePanelTitle(title.value.trim())).data
    panelTitle.value = form.value.title
    message.value = '面板名称已保存'
  } catch (error) {
    fail(error)
  }
}

async function saveAllow() {
  message.value = ''
  messageBad.value = false
  try {
    form.value = (await saveAllowIps(allowIps.value.trim())).data
    allowIps.value = form.value.allow_ips || ''
    message.value = allowIps.value ? '授权 IP 已保存，其他地址不能访问面板' : '授权 IP 已清空，不再限制访问地址'
  } catch (error) {
    fail(error)
  }
}

async function savePassword() {
  message.value = ''
  messageBad.value = false
  try {
    await changePanelPassword(oldPassword.value, newPassword.value)
    oldPassword.value = ''
    newPassword.value = ''
    message.value = '登录密码已修改，下次登录请使用新密码'
  } catch (error) {
    fail(error)
  }
}

async function saveTerminal() {
  message.value = ''
  messageBad.value = false
  try {
    const { data } = await saveTerminalConfig({
      use_completion: form.value.use_completion,
      terminal_theme: form.value.terminal_theme,
      ai_shell: false,
    })
    Object.assign(form.value, data)
    message.value = '终端设置已保存，新开的终端会使用它'
  } catch (error) {
    fail(error)
  }
}

function onOff(value) {
  return value === 'yes' ? '已开启' : '已关闭'
}

onMounted(load)
</script>

<template>
  <div v-if="form" class="mx-auto h-full max-w-3xl overflow-auto">
    <p
      v-if="message"
      class="mb-4 rounded border px-4 py-3 text-sm"
      :class="messageBad ? 'border-[#f3c7c7] bg-[#fff5f5] text-[#c24141]' : 'border-[#cdead4] bg-[#f3faf5] text-[#157a32]'"
    >
      {{ message }}
    </p>

    <section class="rounded border border-panel-line bg-white p-5 shadow-card">
      <h2 class="text-base font-medium">基本信息</h2>
      <p class="mt-1 text-xs text-panel-muted">名称显示在左上角。端口只展示，不会改其他服务。</p>
      <label class="mt-4 block text-xs text-panel-muted">
        面板名称
        <input v-model="title" class="field" maxlength="20" />
      </label>
      <dl class="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div class="rounded bg-[#f7f8fa] px-3 py-2">
          <dt class="text-xs text-panel-muted">登录用户</dt>
          <dd class="mt-1 text-sm">{{ form.username }}</dd>
        </div>
        <div class="rounded bg-[#f7f8fa] px-3 py-2">
          <dt class="text-xs text-panel-muted">监听端口</dt>
          <dd class="mt-1 text-sm">{{ form.port }}</dd>
        </div>
        <div class="rounded bg-[#f7f8fa] px-3 py-2">
          <dt class="text-xs text-panel-muted">SSH 密码登录</dt>
          <dd class="mt-1 text-sm">{{ onOff(form.password) }}</dd>
        </div>
        <div class="rounded bg-[#f7f8fa] px-3 py-2">
          <dt class="text-xs text-panel-muted">SSH 密钥登录</dt>
          <dd class="mt-1 text-sm">{{ onOff(form.pubkey) }}</dd>
        </div>
      </dl>
      <p class="mt-3 text-xs text-panel-muted">SSH 状态只在这里查看。要改的话去安全页，这里不会写 sshd。</p>
      <button type="button" class="btn mt-4" @click="saveTitle">保存名称</button>
    </section>

    <section class="mt-4 rounded border border-panel-line bg-white p-5 shadow-card">
      <div class="flex items-start justify-between gap-4">
        <div>
          <h2 class="text-base font-medium">授权 IP</h2>
          <p class="mt-1 text-xs text-panel-muted">多个地址用英文逗号分隔。留空并保存，表示不限制来源。</p>
        </div>
        <div class="shrink-0 rounded bg-[#f7f8fa] px-3 py-2 text-right">
          <div class="text-xs text-panel-muted">当前访问 IP</div>
          <div class="mt-1 text-sm font-medium">{{ form.client_ip || '未知' }}</div>
        </div>
      </div>
      <input v-model="allowIps" class="field" placeholder="例如 203.0.113.8, 203.0.113.9" />
      <p class="mt-3 rounded border border-[#f3ddb0] bg-[#fff8eb] px-3 py-2 text-xs leading-5 text-[#8a5a12]">
        一旦设置授权 IP，只有这些地址能打开面板。名单里必须包含当前访问 IP，否则不能保存。
      </p>
      <button type="button" class="btn mt-4" @click="saveAllow">保存授权 IP</button>
    </section>

    <section class="mt-4 rounded border border-panel-line bg-white p-5 shadow-card">
      <h2 class="text-base font-medium">登录密码</h2>
      <p class="mt-1 text-xs text-panel-muted">新密码 8 到 72 位，不能包含空格。</p>
      <input v-model="oldPassword" class="field" type="password" placeholder="当前密码" autocomplete="current-password" />
      <input v-model="newPassword" class="field" type="password" placeholder="新密码" autocomplete="new-password" />
      <button type="button" class="btn mt-4" @click="savePassword">修改密码</button>
    </section>

    <section class="mt-4 rounded border border-panel-line bg-white p-5 shadow-card">
      <h2 class="text-base font-medium">终端</h2>
      <p class="mt-1 text-xs text-panel-muted">新开的终端才会用到这些选项。终端以面板用户身份启动，不加载家目录启动脚本。</p>
      <label class="mt-4 flex items-center gap-2 text-sm">
        <input v-model="form.terminal_theme" type="checkbox" /> 使用深色终端主题
      </label>
      <label class="mt-2 flex items-center gap-2 text-sm">
        <input v-model="form.use_completion" type="checkbox" /> 命令补全（只用系统自带的 bash-completion）
      </label>
      <button type="button" class="btn mt-4" @click="saveTerminal">保存终端设置</button>
    </section>
  </div>
</template>

<style scoped>
.field {
  display: block;
  width: 100%;
  margin-top: 8px;
  border: 1px solid #e7e9ed;
  border-radius: 4px;
  background: white;
  padding: 8px 10px;
  font-size: 14px;
}
.btn {
  border-radius: 4px;
  background: #20a53a;
  color: white;
  padding: 6px 14px;
  font-size: 13px;
}
</style>
