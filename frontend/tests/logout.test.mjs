import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const calls = []
const auth = { currentUser:null }
const user = { getIdToken:async () => 'token' }
const result = await build({
  stdin:{ contents:"export * from './src/lib/logout'; export { default as Header } from './src/components/Header'", resolveDir:process.cwd(), loader:'tsx' },
  bundle:true, write:false, platform:'node', format:'cjs', packages:'external',
  loader:{ '.png':'dataurl' }, define:{ 'import.meta.env':'{}' },
  plugins:[{ name:'auth-session', setup(build) {
    build.onResolve({ filter:/(?:^|\/)firebase$/ }, () => ({ path:'test:auth', external:true }))
  } }],
})
const module = { exports:{} }
new Function('require','module','exports',result.outputFiles[0].text)(id => {
  if (id==='test:auth') return { auth }
  if (id==='firebase/auth') return { signOut:async current => { assert.equal(current,auth); calls.push('firebase-signout'); auth.currentUser=null } }
  return require(id)
},module,module.exports)

test('header logout revokes the authenticated account before clearing Firebase', async () => {
  const previous = globalThis.fetch
  auth.currentUser=user; calls.length=0
  globalThis.fetch=async (url,options) => {
    assert.ok(url.endsWith('/auth/logout'))
    assert.equal(options.headers.Authorization,'Bearer token')
    calls.push('backend-revoke')
    return new Response(null,{ status:204 })
  }
  try {
    await module.exports.logoutCurrentSession()
    assert.deepEqual(calls,['backend-revoke','firebase-signout'])
    assert.equal(auth.currentUser,null)
  } finally { globalThis.fetch=previous }
})

test('an expired session can be cleared but a failed revocation is reported', async () => {
  const previous = globalThis.fetch
  try {
    auth.currentUser=user; calls.length=0
    globalThis.fetch=async () => new Response(JSON.stringify({ detail:'Token revocado' }),{ status:401 })
    await module.exports.logoutCurrentSession()
    assert.equal(auth.currentUser,null)
    auth.currentUser={ getIdToken:async () => { throw Object.assign(new Error('Expired'),{ code:'auth/user-token-expired' }) } }
    await module.exports.logoutCurrentSession()
    assert.equal(auth.currentUser,null)
    auth.currentUser=user; calls.length=0
    globalThis.fetch=async () => new Response(JSON.stringify({ detail:'Servidor no disponible' }),{ status:503 })
    await assert.rejects(module.exports.logoutCurrentSession(),/Servidor no disponible/)
    assert.equal(auth.currentUser,user)
    assert.deepEqual(calls,[])
  } finally { globalThis.fetch=previous }
})

test('the common header shows a compact account menu only to authenticated users', () => {
  const { createElement } = require('react')
  const { renderToStaticMarkup } = require('react-dom/server')
  const { MemoryRouter } = require('react-router-dom')
  for (const authenticated of [false,true]) {
    const html = renderToStaticMarkup(createElement(MemoryRouter,{},createElement(module.exports.Header,{ authenticated })))
    assert.equal((html.match(/aria-label="Abrir menú de cuenta"/g)||[]).length,authenticated?1:0)
    assert.doesNotMatch(html, /aria-label="Cerrar sesión"/)
  }
})
