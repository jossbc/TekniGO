import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const result = await build({ entryPoints: ['src/lib/emailVerification.ts'], bundle: true, write: false,
  platform: 'node', format: 'cjs', packages: 'external', define: { 'import.meta.env': '{}' } })
let sent = 0
let failSend = false
const module = { exports: {} }
const mockRequire = (name) => name === 'firebase/auth' ? { sendEmailVerification: async () => {
  if (failSend) throw new Error('offline')
  sent++
} } : require(name)
new Function('require', 'module', 'exports', result.outputFiles[0].text)(mockRequire, module, module.exports)
const { requestEmailVerification, refreshEmailVerification, resendWaitSeconds } = module.exports

test('verification links respect cooldown and reload current user before sending', async () => {
  const previous = globalThis.sessionStorage
  const storage = new Map()
  globalThis.sessionStorage = { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) }
  let reloads = 0
  const user = { uid: 'one', email: 'ana@example.com', emailVerified: false, reload: async () => { reloads++ } }
  try {
    assert.equal(await requestEmailVerification(user), true)
    assert.equal(sent, 1)
    assert.ok(resendWaitSeconds(user) > 0)
    await assert.rejects(requestEmailVerification(user), /Espera/)
    assert.equal(sent, 1)
    user.email = 'new@example.com'
    assert.equal(resendWaitSeconds(user), 0)
    await requestEmailVerification(user)
    assert.equal(sent, 2)
    user.emailVerified = true
    assert.equal(await requestEmailVerification(user), false)
    assert.equal(sent, 2)
    assert.equal(reloads, 4)
  } finally { globalThis.sessionStorage = previous }
})

test('failed delivery can be retried and does not claim success', async () => {
  const user = { uid: 'two', email: 'retry@example.com', emailVerified: false, reload: async () => {} }
  failSend = true
  try { await assert.rejects(requestEmailVerification(user), /offline/) }
  finally { failSend = false }
  assert.equal(resendWaitSeconds(user), 0)
  assert.equal(await requestEmailVerification(user), true)
})

test('confirmation reloads and refreshes token, then uses the server result', async () => {
  const previous = globalThis.fetch
  const calls = []
  const user = { reload: async () => { calls.push('reload') }, getIdToken: async (force) => {
    calls.push(force === true ? 'refresh' : 'token'); return 'token'
  }, emailVerified: true }
  globalThis.fetch = async (url) => {
    assert.ok(url.endsWith('/users/me/email-verification'))
    calls.push('server')
    return new Response(JSON.stringify({ email: 'ana@example.com', email_verified: false }), { status: 200 })
  }
  try {
    assert.equal((await refreshEmailVerification(user)).email_verified, false)
    assert.deepEqual(calls, ['reload', 'refresh', 'token', 'server'])
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'No disponible' }), { status: 503 })
    await assert.rejects(refreshEmailVerification(user), /No disponible/)
  } finally { globalThis.fetch = previous }
})
