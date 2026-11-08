import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const result = await build({ stdin: { contents: "export * from './src/lib/emailVerification'; export { default as EmailVerification } from './src/components/EmailVerification';", resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, write: false,
  platform: 'node', format: 'cjs', packages: 'external', loader: { '.png': 'dataurl' }, define: { 'import.meta.env': '{}' } })
let sent = 0
let failSend = false
const module = { exports: {} }
const mockRequire = (name) => name === 'firebase/auth' ? { sendEmailVerification: async () => {
  if (failSend) throw new Error('offline')
  sent++
} } : require(name)
new Function('require', 'module', 'exports', result.outputFiles[0].text)(mockRequire, module, module.exports)
const { requestEmailVerification, refreshEmailVerification, resendWaitSeconds } = module.exports

const flush = () => new Promise(resolve => setImmediate(resolve))

function browserHarness() {
  const previous = new Map(['window', 'document', 'navigator', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  const timers = new Map()
  let id = 0
  const window = Object.assign(new EventTarget(), {
    setInterval(callback) { timers.set(++id, callback); return id },
    clearInterval(id) { timers.delete(id) },
  })
  const document = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  const navigator = { onLine: true }
  for (const [key, value] of Object.entries({ window, document, navigator })) {
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value })
  }
  return {
    window, document, navigator, timers,
    async tick() { for (const callback of [...timers.values()]) await callback() },
    restore() { for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    } },
  }
}

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

test('verification updates automatically on return to the tab and stops once confirmed', async () => {
  const browser = browserHarness()
  const statuses = []
  let verified = false
  let reloads = 0
  const user = { reload: async () => { reloads++ }, getIdToken: async () => 'token' }
  globalThis.fetch = async () => new Response(JSON.stringify({ email: 'ana@example.com', email_verified: verified }))
  const stop = module.exports.watchEmailVerification(user, value => statuses.push(value), assert.fail)
  try {
    await flush()
    assert.equal(statuses.at(-1).email_verified, false)
    // A confirmation in a different tab/device is discovered without a button.
    verified = true
    browser.window.dispatchEvent(new Event('focus'))
    await flush()
    assert.equal(statuses.at(-1).email_verified, true)
    assert.equal(browser.timers.size, 0)
    browser.window.dispatchEvent(new Event('focus'))
    await browser.tick()
    assert.equal(reloads, 2)
  } finally { stop(); browser.restore() }
})

test('automatic checks pause when hidden or offline and recover after an error', async () => {
  const browser = browserHarness()
  const statuses = [], errors = []
  let requests = 0
  let offline = true
  const user = { reload: async () => {}, getIdToken: async () => 'token' }
  globalThis.fetch = async () => {
    requests++
    if (offline) throw new TypeError('Failed to fetch')
    return new Response(JSON.stringify({ email: 'ana@example.com', email_verified: true }))
  }
  const stop = module.exports.watchEmailVerification(user, value => statuses.push(value), error => errors.push(error))
  try {
    await flush()
    assert.equal(errors.length, 1)
    assert.equal(statuses.length, 0)
    browser.document.visibilityState = 'hidden'
    await browser.tick()
    assert.equal(requests, 1)
    browser.document.visibilityState = 'visible'
    browser.navigator.onLine = false
    await browser.tick()
    assert.equal(requests, 1)
    offline = false
    browser.navigator.onLine = true
    browser.window.dispatchEvent(new Event('online'))
    await flush()
    assert.equal(statuses.at(-1).email_verified, true)
    assert.equal(requests, 2)
  } finally { stop(); browser.restore() }
})

test('checks never overlap and leaving the page ignores in-flight results', async () => {
  const browser = browserHarness()
  let release
  let reloads = 0
  const statuses = [], errors = []
  const user = { reload: () => { reloads++; return new Promise(resolve => { release = resolve }) }, getIdToken: async () => 'token' }
  globalThis.fetch = async () => new Response(JSON.stringify({ email: 'ana@example.com', email_verified: true }))
  const stop = module.exports.watchEmailVerification(user, value => statuses.push(value), error => errors.push(error))
  try {
    browser.window.dispatchEvent(new Event('focus'))
    browser.document.dispatchEvent(new Event('visibilitychange'))
    await browser.tick()
    assert.equal(reloads, 1)
    stop()
    release()
    await flush()
    assert.deepEqual(statuses, [])
    assert.deepEqual(errors, [])
    assert.equal(browser.timers.size, 0)
  } finally { stop(); browser.restore() }
})

test('the email verification screen only offers resending the link', () => {
  const { createElement } = require('react')
  const { renderToStaticMarkup } = require('react-dom/server')
  const html = renderToStaticMarkup(createElement(module.exports.EmailVerification, { user: { uid: 'one', email: 'ana@example.com' } }))
  assert.equal((html.match(/<button\b/g) || []).length, 1)
  assert.match(html, /Reenviar enlace/)
  assert.doesNotMatch(html, /Ya verifiqu/)
})
