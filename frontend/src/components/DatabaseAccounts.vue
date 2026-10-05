<script setup>
import { ref, watch } from 'vue'
import {
  databaseError,
  mongoAddUser,
  mongoDeleteUser,
  mongoUsers,
  mysqlAddUser,
  mysqlDeleteUser,
  mysqlUsers,
  redisConfig,
  redisSaveConfig,
} from '../api/databases'

const props = defineProps({
  engine: { type: String, required: true },
})

const message = ref('')
const users = ref([])
const redis = ref({ maxmemory: 0, policy: 'noeviction', bind: '', databases: '' })
const form = ref({ username: '', password: '', host: 'localhost', db_name: '' })

async function load() {
  message.value = ''
  users.value = []
  try {
    if (props.engine === 'mysql') {
      users.value = (await mysqlUsers()).data.items || []
    } else if (props.engine === 'mongodb') {
      users.value = (await mongoUsers()).data.items || []
    } else if (props.engine === 'redis') {
      const { data } = await redisConfig()
      redis.value = {
        maxmemory: Number(data.maxmemory || 0),
        policy: data['maxmemory-policy'] || 'noeviction',
        bind: data.bind || '',
        databases: data.databases || '',
      }
    }
  } catch (error) {
    message.value = databaseError(error)
  }
}

async function addUser() {
  try {
    if (props.engine === 'mysql') await mysqlAddUser(form.value)
    else await mongoAddUser(form.value)
    form.value.password = ''
    message.value = '用户已保存，只授予这个库的读写权限'
    await load()
  } catch (error) {
    message.value = databaseError(error)
  }
}

async function removeUser(row) {
  try {
    if (props.engine === 'mysql') await mysqlDeleteUser(row.username, row.host)
    else await mongoDeleteUser(row.username, row.db)
    await load()
  } catch (error) {
    message.value = databaseError(error)
  }
}

async function saveRedis() {
  try {
    await redisSaveConfig(Number(redis.value.maxmemory), redis.value.policy)
    message.value = 'Redis 内存配置已保存，监听地址没有改动'
    await load()
  } catch (error) {
    message.value = databaseError(error)
  }
}

watch(() => props.engine, load, { immediate: true })
</script>

<template>
  <section class="mb-4 rounded border border-panel-line p-3">
    <h3 class="text-sm font-medium">{{ engine === 'redis' ? 'Redis 配置' : '用户权限' }}</h3>
    <p v-if="message" class="mt-2 text-xs text-panel-muted">{{ message }}</p>

    <div v-if="engine === 'redis'" class="mt-3 grid gap-2 md:grid-cols-4">
      <label class="text-xs text-panel-muted">最大内存（字节，不超过 256MB）
        <input v-model="redis.maxmemory" class="box" type="number" min="0" max="268435456" />
      </label>
      <label class="text-xs text-panel-muted">淘汰策略
        <select v-model="redis.policy" class="box">
          <option value="noeviction">noeviction</option>
          <option value="allkeys-lru">allkeys-lru</option>
          <option value="volatile-lru">volatile-lru</option>
        </select>
      </label>
      <div class="text-xs text-panel-muted">监听<input class="box" :value="redis.bind || '127.0.0.1'" disabled /></div>
      <button type="button" class="btn self-end" @click="saveRedis">保存配置</button>
    </div>

    <template v-else>
      <div class="mt-3 grid gap-2 md:grid-cols-5">
        <input v-model="form.username" class="box" placeholder="用户名" />
        <input v-model="form.password" class="box" type="password" placeholder="密码" />
        <input v-if="engine === 'mysql'" v-model="form.host" class="box" placeholder="localhost" />
        <input v-model="form.db_name" class="box" placeholder="数据库名" />
        <button type="button" class="btn" @click="addUser">添加用户</button>
      </div>
      <table class="mt-3 w-full text-left text-sm">
        <thead class="text-xs text-panel-muted">
          <tr><th>用户</th><th>{{ engine === 'mysql' ? '主机' : '库' }}</th><th>权限</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="row in users" :key="`${row.username}-${row.host || row.db}`" class="border-t border-panel-line">
            <td class="py-2">{{ row.username }}</td>
            <td>{{ row.host || row.db }}</td>
            <td class="max-w-md truncate">{{ row.grants || (row.roles || []).map((item) => item.role).join('、') }}</td>
            <td>
              <button v-if="!row.reserved && row.username !== 'panel'" type="button" class="text-xs text-[#c24141]" @click="removeUser(row)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<style scoped>
.box { width: 100%; border: 1px solid #e7e9ed; border-radius: 4px; padding: 6px 8px; }
.btn { border-radius: 4px; background: #20a53a; color: white; padding: 6px 12px; }
</style>
