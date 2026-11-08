import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const calls = []
const user = { uid: 'same-user', getIdToken: async refresh => { calls.push(['token', refresh]); return 'token' } }
let failUpdate = false
const result = await build({
  entryPoints: ['src/lib/phoneVerification.ts'], bundle: true, write: false, platform: 'node', format: 'cjs',
  packages: 'external', define: { 'import.meta.env': '{}' },
  plugins: [{ name: 'phone-provider', setup(build) {
    build.onResolve({ filter: /(?:^|\/)firebase$/ }, () => ({ path: 'test:auth', external: true }))
  } }],
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(id => {
  if (id === 'test:auth') return { auth: { currentUser: user } }
  if (id === 'firebase/auth') return {
    PhoneAuthProvider: Object.assign(class {
      async verifyPhoneNumber(phone, verifier) { calls.push(['send', phone, verifier]); return 'verification-id' }
    }, { credential(id, code) { return { id, code } } }),
    async updatePhoneNumber(identity, credential) {
      calls.push(['update', identity, credential])
      if (failUpdate) throw Object.assign(new Error('Bad code'), { code: 'auth/invalid-verification-code' })
    },
  }
  return require(id)
}, module, module.exports)

test('SMS uses the saved number and confirms on the existing account before checking the backend', async () => {
  const originalFetch = globalThis.fetch
  calls.length = 0
  globalThis.fetch = async (_url, options) => {
    calls.push(['api', options.method])
    return new Response(JSON.stringify({ telefono: '+50498765432', telefono_verificado: options.method === 'POST' }))
  }
  try {
    const verifier = {}
    assert.equal(await module.exports.sendPhoneCode(user, verifier), 'verification-id')
    assert.deepEqual(calls.find(c => c[0] === 'send'), ['send', '+50498765432', verifier])
    calls.length = 0
    assert.equal((await module.exports.confirmPhoneCode(user, 'verification-id', '123456')).telefono_verificado, true)
    assert.deepEqual(calls.map(c => c[0]), ['update', 'token', 'token', 'api'])
    assert.equal(calls[0][1], user)
    assert.equal(calls[1][1], true)
  } finally { globalThis.fetch = originalFetch }
})

test('invalid or rejected SMS codes cannot mark the phone as verified', async () => {
  const originalFetch = globalThis.fetch
  let requests = 0
  globalThis.fetch = async () => { requests++; throw new Error('Unexpected backend call') }
  try {
    for (const code of ['', '12345', '1234567', 'abcdef']) {
      await assert.rejects(module.exports.confirmPhoneCode(user, 'id', code), /6 dígitos/)
    }
    failUpdate = true
    await assert.rejects(module.exports.confirmPhoneCode(user, 'id', '123456'), /Bad code/)
    assert.equal(requests, 0)
  } finally { failUpdate = false; globalThis.fetch = originalFetch }
})
