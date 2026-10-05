import axios from 'axios'
import { apiBaseURL, stripApiPrefix } from './origin'

// 不挂 Token 拦截器。登录、刷新、退出都走这里，避免和 401 自动刷新缠在一起。
export const raw = axios.create({
  baseURL: apiBaseURL(),
  timeout: 10000,
})

raw.interceptors.request.use((config) => {
  config.url = stripApiPrefix(config.url)
  return config
})
