import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const { createElement } = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { MemoryRouter } = require('react-router-dom')
const result = await build({
  stdin: { contents: `export * from './src/lib/api'; export * from './src/lib/account'; export * from './src/components/AccountStatus';`, resolveDir: process.cwd(), loader: 'tsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
  define: { 'import.meta.env': '{}' },
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
const { loadAccount, api, ApiError, AccountStatus } = module.exports
const user = { getIdToken: async () => 'token' }
const profile = { roles: ['CLIENTE'], estado_identidad: 'PENDIENTE', nombre_completo: 'Ana' }
const response = (status, body) => new Response(JSON.stringify(body), { status })

test('account loading distinguishes pending verification, missing registration, errors and invalid sessions', async () => {
  const original = globalThis.fetch
  try {
    globalThis.fetch = async () => response(200, profile)
    assert.deepEqual(await loadAccount(user), { status: 'ready', profile })
    globalThis.fetch = async () => response(404, { detail: 'Completa tu registro en TekniGO' })
    assert.deepEqual(await loadAccount(user), { status: 'missing' })
    for (const status of [404, 500]) {
      globalThis.fetch = async () => response(status, { detail: 'Servicio no disponible' })
      assert.equal((await loadAccount(user)).status, 'error')
      await assert.rejects(api('/users/me', user), (error) => error instanceof ApiError && error.status === status)
    }
    globalThis.fetch = async () => { throw new TypeError('Failed to fetch') }
    assert.equal((await loadAccount(user)).status, 'error')
    globalThis.fetch = async () => response(401, { detail: 'Token revocado' })
    assert.deepEqual(await loadAccount(user), { status: 'unauthorized' })
  } finally { globalThis.fetch = original }
})

test('401 refreshes the token once and recovers, without retrying indefinitely', async () => {
  const original = globalThis.fetch
  const tokens = []
  let calls = 0
  try {
    globalThis.fetch = async (_url, options) => {
      calls++
      assert.equal(options.headers.Authorization, calls === 1 ? 'Bearer cached' : 'Bearer refreshed')
      return calls === 1 ? response(401, {}) : response(200, profile)
    }
    const identity = { getIdToken: async (force) => { tokens.push(force); return force ? 'refreshed' : 'cached' } }
    assert.equal((await loadAccount(identity)).status, 'ready')
    assert.deepEqual(tokens, [undefined, true])
    assert.equal(calls, 2)
  } finally { globalThis.fetch = original }
})

test('a slow account request keeps loading until it resolves', async () => {
  const original = globalThis.fetch
  let release
  try {
    globalThis.fetch = () => new Promise((resolve) => { release = resolve })
    let settled = false
    const pending = loadAccount(user).then((value) => { settled = true; return value })
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(settled, false)
    release(response(200, profile))
    assert.equal((await pending).status, 'ready')
  } finally { globalThis.fetch = original }
})

test('only confirmed missing registration shows the registration action', () => {
  for (const state of [{ status: 'loading' }, { status: 'error', message: 'Error de conexión' }, { status: 'unauthorized' }, { status: 'missing' }]) {
    const html = renderToStaticMarkup(createElement(MemoryRouter, {}, createElement(AccountStatus, { state, retry() {} })))
    assert.equal(html.includes('Completar mi registro'), state.status === 'missing')
    if (state.status === 'loading') assert.match(html, /Cargando tu cuenta/)
    if (state.status === 'error') assert.match(html, /Reintentar/)
    if (state.status === 'unauthorized') assert.match(html, /Volver a iniciar sesión/)
  }
})
