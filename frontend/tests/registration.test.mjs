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
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', loader: { '.png': 'dataurl', '.jpg': 'dataurl', '.css': 'empty' },
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

test('HU-01 requests registration fields and password confirmation with or without an existing session', async () => {
  const result = await build({
    entryPoints: ['src/App.tsx'], bundle: true, write: false,
    platform: 'node', format: 'cjs', packages: 'external', loader: { '.png': 'dataurl', '.jpg': 'dataurl', '.css': 'empty' },
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
    for (const name of ['name', 'email', 'phone', 'dni', 'password', 'passwordConfirmation']) {
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
  const landing = renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: ['/'] },
    createElement(module.exports.default)))
  assert.match(landing, /Tu hogar necesita/)
  assert.match(landing, /href="\/ingresar"/)
  assert.match(landing, /href="\/registro"/)
  assert.doesNotMatch(landing, /name="password"/)
  const technicianRegistration = renderToStaticMarkup(createElement(MemoryRouter, {
    initialEntries: [{ pathname: '/registro', state: { role: 'TECNICO' } }],
  }, createElement(AccessPage, { mode: 'register', user: null })))
  assert.match(technicianRegistration, /<button[^>]*aria-pressed="true"[^>]*>[\s\S]*?Ofrezco mis servicios/)
  assert.match(technicianRegistration, /<button[^>]*class="selected"[^>]*aria-pressed="true"[^>]*>[\s\S]*?Ofrezco mis servicios/)
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

// Registration submission: delayed responses, password confirmation and recovery.
{
  const React = require('react')
  let harness
  const result = await build({
    entryPoints: ['src/App.tsx'], bundle: true, write: false, platform: 'node', format: 'cjs',
    packages: 'external', loader: { '.png': 'dataurl', '.jpg': 'dataurl', '.css': 'empty' }, define: { 'import.meta.env': '{}' },
    plugins: [{ name: 'registration-dependencies', setup(build) {
      build.onResolve({ filter: /(?:^|\/)firebase$/ }, () => ({ path: 'test:auth', external: true }))
      build.onResolve({ filter: /(?:^|\/)emailVerification$/ }, () => ({ path: 'test:verification', external: true }))
    }}],
  })
  const m = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)((id) => {
    if (id === 'react') return { ...React,
      useState(initial) { const i = harness.index++; if (!(i in harness.state)) harness.state[i] = initial; return [harness.state[i], value => { harness.state[i] = value }] },
      useEffect() {},
    }
    if (id === 'react-router-dom') return { ...require(id), useNavigate: () => (...args) => harness.navigation.push(args), useLocation: () => ({ state: null }) }
    if (id === 'test:auth') return { auth: {} }
    if (id === 'test:verification') return { requestEmailVerification: () => harness.verification() }
    if (id === 'firebase/auth') return { ...require(id),
      createUserWithEmailAndPassword: async () => { harness.created++; return { user: { getIdToken: async () => 'test-token' } } },
      reauthenticateWithCredential: async () => {},
    }
    return require(id)
  }, m, m.exports)
  const nodes = tree => !tree || typeof tree !== 'object' ? [] : [tree, ...React.Children.toArray(tree.props?.children).flatMap(nodes)]
  function render(user = null) { harness.index = 0; return nodes(m.exports.AccessPage({ mode: 'register', user })) }
  function setup() { harness = { index: 0, state: [], navigation: [], created: 0, verification: async () => false }; return harness }
  function event(passwordConfirmation = 'Password123') {
    const data = new FormData()
    for (const [key, value] of Object.entries({email: 'test@example.com',password: 'Password123',passwordConfirmation,phone:'98765432',name:'Prueba',dni:'0801200000000'})) data.set(key, value)
    return { preventDefault() {}, currentTarget: data }
  }
  const submit = (tree, e) => tree.find(n => n.type === 'form').props.onSubmit(e)
  const pending = tree => tree.some(n => n.props?.message === 'Creando tu perfil.')

  test('registration stays in loading through session changes and verification delivery, then navigates', async () => {
    const h = setup()
    const originalFormData = globalThis.FormData
    const originalFetch = globalThis.fetch
    let releaseSave, releaseEmail
    globalThis.fetch = () => new Promise(resolve => { releaseSave = () => resolve(new Response('{}', {status:200})) })
    h.verification = () => new Promise(resolve => { releaseEmail = () => resolve(true) })
    const e = event()
    globalThis.FormData = class { constructor(value) { return value } }
    try {
      const task = submit(render(), e)
      assert.equal(pending(render()), true)
      const duringSessionChange = render({uid:'new-user',email:'test@example.com'})
      assert.equal(pending(duringSessionChange), true)
      assert.equal(duringSessionChange.find(n => n.props?.className === 'site').props.hidden, true)
      assert.ok(duringSessionChange.some(n => n.type === 'form'), 'keep form mounted for recoverable errors')
      await new Promise(resolve => setImmediate(resolve))
      releaseSave()
      await new Promise(resolve => setImmediate(resolve))
      assert.equal(h.navigation.length, 0)
      assert.equal(pending(render()), true)
      releaseEmail()
      await task
      assert.equal(h.created, 1)
      assert.equal(h.navigation[0][0], '/registro/completar')
      assert.equal(pending(render()), true, 'do not flash form before route commits')
    } finally { globalThis.FormData = originalFormData; globalThis.fetch = originalFetch }
  })

  test('mismatched passwords never create an account, and matching input clears native validation', async () => {
    const h = setup()
    const original = globalThis.FormData
    const e = event('Different123')
    globalThis.FormData = class { constructor(value) { return value } }
    try {
      await submit(render(), e)
      assert.equal(h.created, 0)
      assert.equal(pending(render()), false)
      assert.ok(render().some(n => n.props?.role === 'alert' && /no coinciden/.test(n.props.children)))
      let message
      const password = {value:'Password123'}
      const confirmation = {value:'Different123',setCustomValidity(value) { message = value }}
      const inputEvent = {currentTarget:{elements:{namedItem(name) { return name === 'password' ? password : confirmation }}}}
      const form = render().find(n => n.type === 'form')
      form.props.onInput(inputEvent)
      assert.match(message, /no coinciden/)
      confirmation.value = password.value
      form.props.onInput(inputEvent)
      assert.equal(message, '')
    } finally { globalThis.FormData = original }
  })

  test('failed profile save restores the registration form with an error', async () => {
    setup()
    const originalFormData = globalThis.FormData
    const originalFetch = globalThis.fetch
    const e = event()
    globalThis.FormData = class { constructor(value) { return value } }
    globalThis.fetch = async () => new Response(JSON.stringify({detail:'No pudimos guardar el perfil'}), {status:500})
    try {
      await submit(render(), e)
      const tree = render()
      assert.equal(pending(tree), false)
      assert.equal(tree.find(n => n.props?.className === 'site').props.hidden, false)
      assert.ok(tree.some(n => n.props?.role === 'alert' && /guardar el perfil/.test(n.props.children)))
      assert.equal(harness.navigation.length, 0)
    } finally { globalThis.FormData = originalFormData; globalThis.fetch = originalFetch }
  })
}
