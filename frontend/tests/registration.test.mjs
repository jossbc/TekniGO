import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const { createElement } = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { MemoryRouter } = require('react-router-dom')

test('pending account and document requests do not display registration or upload forms', async () => {
  const result = await build({
    stdin: { contents: `export { default as CompleteRegistration } from './src/CompleteRegistration'; export { default as IdentityVerification } from './src/components/ClientIdentityVerification';`, resolveDir: process.cwd(), loader: 'tsx' },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' },
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
  const user = { uid: 'loading-user', getIdToken: async () => 'token' }
  const registration = renderToStaticMarkup(createElement(MemoryRouter, {}, createElement(module.exports.CompleteRegistration, { user })))
  assert.match(registration, /Cargando tu cuenta/)
  assert.doesNotMatch(registration, /PASO 2|Completa tu registro|Finalizar registro/)
  const documents = renderToStaticMarkup(createElement(module.exports.IdentityVerification, { user }))
  assert.match(documents, /Consultando el estado/)
  assert.doesNotMatch(documents, /type="file"|Enviar para/)
})

test('registration resumes from saved requirements and distinguishes review from missing documents', async () => {
  const result = await build({
    entryPoints: ['src/lib/registration.ts'], bundle: true, write: false,
    platform: 'node', format: 'cjs', define: { 'import.meta.env': '{}' },
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
  const { registrationRequirements, registrationComplete } = module.exports
  const originalFetch = globalThis.fetch
  const user = { getIdToken: async () => 'test-token' }
  let identity = { estado_identidad: 'EN_REVISION', documentos: ['DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE'].map(tipo => ({ tipo })) }
  let specialties = []
  let zones = []
  globalThis.fetch = async (url) => {
    const data = url.endsWith('/identity-documents') ? identity : url.endsWith('/specialties') ? specialties : zones
    return new Response(JSON.stringify(data), { status: 200 })
  }
  try {
    assert.equal(registrationComplete(await registrationRequirements(user, { roles: ['CLIENTE'] })), true)
    assert.equal(registrationComplete(await registrationRequirements(user, { roles: ['TECNICO'] })), false)
    specialties = [{ id: 'electricidad' }]
    assert.equal(registrationComplete(await registrationRequirements(user, { roles: ['TECNICO'] })), false)
    zones = [{ municipio: 'Distrito Central' }]
    assert.equal(registrationComplete(await registrationRequirements(user, { roles: ['TECNICO', 'CLIENTE'] })), true)
    identity.documentos = [{ tipo: 'SELFIE' }, { tipo: 'SELFIE' }, { tipo: 'SELFIE' }]
    assert.equal(registrationComplete(await registrationRequirements(user, { roles: ['CLIENTE'] })), false)
    identity.estado_identidad = 'RECHAZADO'
    assert.equal(registrationComplete(await registrationRequirements(user, { roles: ['CLIENTE'] })), false)
    identity = { estado_identidad: 'VERIFICADO', documentos: [] }
    assert.equal(registrationComplete(await registrationRequirements(user, { roles: ['CLIENTE'] })), true)
    globalThis.fetch = async () => { throw new TypeError('Failed to fetch') }
    await assert.rejects(() => registrationRequirements(user, { roles: ['CLIENTE'] }))
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('cellular numbers accept formatting and reject invalid lengths, prefixes and countries', async () => {
  const result = await build({entryPoints: ['src/lib/validation.ts'], bundle: true, write: false, platform: 'node', format: 'cjs'})
  const module = {exports: {}}
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
  const { normalizeHonduranPhone } = module.exports
  for (const value of ['98765432', '9876-5432', '+504 9876-5432', ' +504 (9876) 5432 ']) {
    assert.equal(normalizeHonduranPhone(value), '+50498765432')
  }
  for (const value of ['87654321', '34567890']) assert.equal(normalizeHonduranPhone(value), '+504' + value)
  const invalid = ['+504 00000', '00000000', '9876543', '987654321', '+50398765432', '+504987654321', '9876A432', '++50498765432']
  for (const prefix of [0, 1, 2, 4, 5, 6, 7]) invalid.push(`${prefix}1234567`)
  for (const value of invalid) assert.throws(() => normalizeHonduranPhone(value))
})

test('HU-01 requests all five fields with or without an existing session', async () => {
  const result = await build({
    entryPoints: ['src/App.tsx'], bundle: true, write: false,
    platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' },
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
  const { AccessPage, accountPath } = module.exports
  assert.equal(accountPath(['CLIENTE']), '/cliente')
  assert.equal(accountPath(['TECNICO']), '/tecnico')
  assert.equal(accountPath(['CLIENTE', 'TECNICO']), '/mi-cuenta')
  for (const user of [null, { uid: 'existing-test-user', email: 'cliente@example.com' }]) {
    const html = renderToStaticMarkup(createElement(MemoryRouter, {},
      createElement(AccessPage, { mode: 'register', user })))
    for (const name of ['name', 'email', 'phone', 'dni', 'password']) {
      const input = html.match(new RegExp(`<input[^>]*name="${name}"[^>]*>`))?.[0]
      assert.ok(input, `Missing field ${name} with session=${!!user}`)
      assert.match(input, /required=""/, `${name} must be required`)
    }
    assert.match(html.match(/<input[^>]*name="email"[^>]*>/)[0], /type="email"/)
    if (user) {
      assert.match(html, /value="cliente@example.com"/)
      assert.match(html, /readOnly=""/)
      assert.match(html, /autoComplete="current-password"/)
    } else {
      assert.match(html, /autoComplete="new-password"/)
    }
  }
})


test('HU-15 validates identity document image formats and size limits before upload', async () => {
  const result = await build({entryPoints: ['src/lib/validation.ts'], bundle: true, write: false, platform: 'node', format: 'cjs'})
  const module = {exports: {}}
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
  const { validateIdentityImage, MAX_IMAGE_SIZE_BYTES } = module.exports

  for (const type of ['image/jpeg', 'image/png', 'image/webp']) {
    assert.equal(validateIdentityImage({ size: 2048, type }, 'el DNI (frente)'), '')
  }
  assert.match(validateIdentityImage(null, 'el DNI (frente)'), /Debes adjuntar/)
  assert.match(validateIdentityImage({ size: 0, type: 'image/jpeg' }, 'la selfie'), /vacío/)
  assert.match(validateIdentityImage({ size: 1024, type: 'application/pdf' }, 'el DNI (reverso)'), /formato/)
  assert.match(validateIdentityImage({ size: MAX_IMAGE_SIZE_BYTES + 1, type: 'image/png' }, 'la selfie'), /5 MB/)
})
