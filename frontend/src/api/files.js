import axios from 'axios'
import { http } from './http'

export function listFiles(path = '', { page = 1, pageSize = 100, q = '', signal } = {}) {
  return http.get('/api/files/list', {
    params: { path, page, page_size: pageSize, q },
    timeout: 5000,
    signal,
  })
}

export function listTree(path = '') {
  return http.get('/api/files/tree', { params: { path }, timeout: 5000, cancelKey: 'files:tree' })
}

export function readFile(path) {
  return http.get('/api/files/content', { params: { path }, timeout: 5000, cancelKey: 'files:read' })
}

export function saveFile(path, content) {
  return http.post('/api/files/content', { path, content })
}

export function createFolder(directory, name) {
  return http.post('/api/files/mkdir', { directory, name })
}

export function createFile(directory, name) {
  return http.post('/api/files/create', { directory, name })
}

export function deletePaths(paths) {
  return http.post('/api/files/delete', { paths })
}

export function renamePath(path, newName) {
  return http.post('/api/files/rename', { path, new_name: newName })
}

export function changeMode(paths, mode) {
  return http.post('/api/files/chmod', { paths, mode })
}

export function compressPaths(paths, dest) {
  return http.post('/api/files/compress', { paths, dest })
}

export function extractArchive(path, dest = '') {
  return http.post('/api/files/extract', { path, dest })
}

export function initUpload(payload) {
  return http.post('/api/files/upload/init', payload)
}

export function uploadChunk(uploadId, index, blob, filename) {
  const form = new FormData()
  form.append('upload_id', uploadId)
  form.append('index', String(index))
  form.append('file', blob, filename)
  return http.post('/api/files/upload/chunk', form)
}

export function completeUpload(uploadId) {
  return http.post('/api/files/upload/complete', { upload_id: uploadId })
}

export function downloadFile(path) {
  return http.get('/api/files/download', { params: { path }, responseType: 'blob', timeout: 0 })
}

export function fileError(error) {
  if (
    axios.isCancel(error)
    || error?.code === 'ERR_CANCELED'
    || error?.name === 'AbortError'
    || error?.cause?.name === 'AbortError'
  ) {
    return { canceled: true, jail: false, message: '' }
  }
  if (error?.code === 'ECONNABORTED') {
    return { jail: false, timeout: true, message: '读取超时，请重试' }
  }
  const detail = error?.response?.data?.detail
  if (detail && typeof detail === 'object') {
    return {
      jail: detail.code === 'path_jail',
      message: detail.message || '已拦截越界路径访问',
    }
  }
  if (typeof detail === 'string') return { jail: false, message: detail }
  return { jail: false, message: '操作失败，请稍后重试' }
}
