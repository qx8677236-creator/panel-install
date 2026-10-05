<script setup>
import { onMounted, ref } from 'vue'
import { changePanelPassword, panelError, panelSettings, panelTitle, savePanelTitle, saveTerminalConfig } from '../api/panel'

const form = ref(null)
const title = ref('')
const oldPassword = ref('')
const newPassword = ref('')
const message = ref('')

async function load() {
  const { data } = await panelSettings()
  form.value = data
  title.value = data.title || ''
  panelTitle.value = title.value || '删库跑路快捷助手'
}

async function saveTitle() {
  message.value = ''
  try {
    form.value = (await savePanelTitle(title.value.trim())).data
    panelTitle.value = form.value.title
    message.value = '面板名称已保存'
  } catch (error) {
    message.value = panelError(error)
  }
}

async function savePassword() {
  message.value = ''
  try {
    await changePanelPassword(oldPassword.value, newPassword.value)
    oldPassword.value = ''
    newPassword.value = ''
    message.value = '登录密码已修改，下次登录请使用新密码'
  } catch (error) {
    message.value = panelError(error)
  }
}

async function saveTerminal() {
  message.value = ''
  try {
    const { data } = await saveTerminalConfig({
      use_completion: form.value.use_completion,
      terminal_theme: form.value.terminal_theme,
      ai_shell: false,
    })
    Object.assign(form.value, data)
    message.value = '终端设置已保存，新开的终端会使用它'
  } catch (error) {
    message.value = panelError(error)
  }
}

onMounted(load)
</script>

<template>
  <div v-if="form" class="h-full overflow-auto p-4">
    <p v-if="message" class="mb-3 text-sm text-panel-green">{{ message }}</p>
    <section class="max-w-xl rounded border border-panel-line bg-white p-4">
      <h2 class="text-base font-medium">面板设置</h2>
      <label class="mt-4 block text-xs text-panel-muted">面板名称
        <input v-model="title" class="box mt-1" maxlength="20" />
      </label>
      <button type="button" class="btn mt-3" @click="saveTitle">保存名称</button>
      <div class="mt-4 text-sm">登录用户：{{ form.username }}</div>
      <div class="mt-1 text-sm">监听端口：{{ form.port }}（只读，不会改其他服务的端口）</div>
      <div class="mt-4 border-t border-panel-line pt-4 text-sm">
        <div>SSH 密码登录：{{ form.password }}</div>
        <div class="mt-1">SSH 密钥登录：{{ form.pubkey }}</div>
        <p class="mt-1 text-xs text-panel-muted">这里只显示状态。修改 SSH 会在安全页单独处理，当前不会写 sshd。</p>
      </div>
    </section>

    <section class="mt-4 max-w-xl rounded border border-panel-line bg-white p-4">
      <h2 class="text-base font-medium">修改登录密码</h2>
      <input v-model="oldPassword" class="box mt-3" type="password" placeholder="当前密码" autocomplete="current-password" />
      <input v-model="newPassword" class="box mt-2" type="password" placeholder="新密码，8 到 72 位" autocomplete="new-password" />
      <button type="button" class="btn mt-3" @click="savePassword">修改密码</button>
    </section>

    <section class="mt-4 max-w-xl rounded border border-panel-line bg-white p-4">
      <h2 class="text-base font-medium">终端</h2>
      <label class="mt-3 flex items-center gap-2 text-sm">
        <input v-model="form.terminal_theme" type="checkbox" /> 使用深色终端主题
      </label>
      <label class="mt-2 flex items-center gap-2 text-sm">
        <input v-model="form.use_completion" type="checkbox" /> 命令补全（只用系统自带的 bash-completion）
      </label>
      <p class="mt-2 text-xs text-panel-muted">AI Shell 保持关闭，不会从外部下载插件。终端以面板用户身份启动，不加载家目录启动脚本。</p>
      <button type="button" class="btn mt-3" @click="saveTerminal">保存终端设置</button>
    </section>
  </div>
</template>

<style scoped>
.box { width: 100%; border: 1px solid #e7e9ed; border-radius: 4px; padding: 6px 8px; }
.btn { border-radius: 4px; background: #20a53a; color: white; padding: 6px 12px; }
</style>
