const BASE = '/api'

async function req(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  health: () => req('/health'),
  today: () => req('/today'),
  memories: (params = {}) => req('/memories?' + new URLSearchParams(params)),
  persona: () => req('/persona'),
  engram: (params = {}) => req('/engram?' + new URLSearchParams(params)),
  emotions: (limit = 50) => req('/emotions?limit=' + limit),
  stamps: () => req('/stamps'),
  writeDiary: (text) => req('/diary', { method: 'POST', body: JSON.stringify({ text }) }),
  writeStamp: (payload) => req('/stamp', { method: 'POST', body: JSON.stringify(payload) }),
}
