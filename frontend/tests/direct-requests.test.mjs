import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

async function loadDirectRequestsClient(env) {
  const result = await build({
    entryPoints: ['src/lib/directRequests.ts'],
    bundle: true,
    write: false,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    define: { 'import.meta.env': JSON.stringify(env) },
  })

  let currentUser = {
    getIdToken: async (forceRefresh = false) => (forceRefresh ? 'fresh-token' : 'initial-token'),
  }

  const module = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(id => {
    if (id === 'firebase/auth') {
      return {
        getAuth: () => ({ currentUser }),
      }
    }
    throw new Error(`Unexpected import: ${id}`)
  }, module, module.exports)

  return {
    ...module.exports,
    setCurrentUser(user) {
      currentUser = user
    },
  }
}

test('createDirectRequest uses relative same-origin route for local/Docker builds with localhost VITE_API_URL', async t => {
  const client = await loadDirectRequestsClient({ VITE_API_URL: 'http://localhost:8000' })
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options })
    return {
      ok: true,
      status: 200,
      json: async () => ({ message: 'Solicitud creada exitosamente', id: 'req-123', estado: 'PENDIENTE' }),
    }
  })

  const payload = {
    tecnico_id: 'tech-uuid-1',
    servicio_id: 'srv-uuid-1',
    municipio: 'Distrito Central',
    sector: 'Col. Palmira',
    direccion_exacta: 'Calle principal #12',
    fecha_hora: '2026-12-01T15:00:00.000Z',
    descripcion: 'Instalación de tomacorrientes',
    cantidad: 2,
    metodo_pago: 'Efectivo',
  }

  const response = await client.createDirectRequest(payload)
  assert.deepEqual(response, { message: 'Solicitud creada exitosamente', id: 'req-123', estado: 'PENDIENTE' })
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/direct-requests/')
  assert.equal(calls[0].options.method, 'POST')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer initial-token')
  assert.deepEqual(JSON.parse(calls[0].options.body), payload)
})

test('createDirectRequest uses relative same-origin route when VITE_API_URL is 127.0.0.1 or unset', async t => {
  const client = await loadDirectRequestsClient({})
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options })
    return {
      ok: true,
      status: 200,
      json: async () => ({ message: 'Solicitud creada exitosamente', id: 'req-456', estado: 'PENDIENTE' }),
    }
  })

  await client.createDirectRequest({
    tecnico_id: 'tech-uuid-1',
    servicio_id: 'srv-uuid-1',
    municipio: 'Distrito Central',
    sector: 'Col. Palmira',
    direccion_exacta: 'Calle principal #12',
    fecha_hora: '2026-12-01T15:00:00.000Z',
    descripcion: 'Instalación de tomacorrientes',
    cantidad: 1,
    metodo_pago: 'Efectivo',
  })

  assert.equal(calls[0].url, '/direct-requests/')
})

test('estimateBudget refreshes expired token on 401 and respects deployed VITE_API_URL', async t => {
  const client = await loadDirectRequestsClient({ VITE_API_URL: 'https://api.teknigo.app/' })
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options })
    if (calls.length === 1) {
      return {
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Token vencido' }),
      }
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ tarifa_base: 250, cantidad: 2, unidad_cobro: 'POR_SERVICIO', total: 500 }),
    }
  })

  const estimate = await client.estimateBudget('srv-uuid-1', 2)
  assert.equal(estimate.total, 500)
  assert.equal(calls.length, 2)
  assert.equal(calls[0].url, 'https://api.teknigo.app/direct-requests/estimate')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer initial-token')
  assert.equal(calls[1].options.headers.Authorization, 'Bearer fresh-token')
})
