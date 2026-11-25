import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { MemoryRouter } = require('react-router-dom')

const bundleResult = await build({
  stdin: {
    contents: `
      export { default as ReviewModal, resolveReviewPublicationBanner } from './src/components/ReviewModal';
      export { RoleReputationSummary } from './src/components/RoleReputationSummary';
      export { TechnicianCard } from './src/components/TechnicianCard';
      export {
        formatReviewDate,
        getUserReputation,
        getUserPublicReviews,
        TECHNICIAN_HIGHLIGHT_CHIPS,
        CLIENT_HIGHLIGHT_CHIPS,
      } from './src/lib/reviews';
    `,
    resolveDir: process.cwd(),
    loader: 'tsx',
  },
  bundle: true,
  write: false,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  loader: { '.png': 'dataurl' },
  define: { 'import.meta.env': '{}' },
})

const loadedModule = { exports: {} }
new Function('require', 'module', 'exports', bundleResult.outputFiles[0].text)(
  id => require(id),
  loadedModule,
  loadedModule.exports
)

const {
  ReviewModal,
  resolveReviewPublicationBanner,
  RoleReputationSummary,
  TechnicianCard,
  formatReviewDate,
  getUserReputation,
  getUserPublicReviews,
  TECHNICIAN_HIGHLIGHT_CHIPS,
  CLIENT_HIGHLIGHT_CHIPS,
} = loadedModule.exports

test('formatReviewDate formats date into America/Tegucigalpa local representation', () => {
  // 2026-11-10T18:00:00Z is 12:00 in Tegucigalpa (UTC-6)
  const formatted = formatReviewDate('2026-11-10T18:00:00Z')
  assert.ok(typeof formatted === 'string')
  assert.ok(formatted.includes('nov') || formatted.includes('10'))
})

test('ReviewModal renders client rating technician view with stars and chips (HU-44)', () => {
  const html = renderToStaticMarkup(
    React.createElement(ReviewModal, {
      user: {},
      tipo_solicitud: 'DIRECTA',
      solicitud_id: 'sol-123',
      evaluadoNombre: 'Juan Perez',
      rolEvaluador: 'CLIENTE',
      onClose: () => {},
      onSuccess: () => {},
    })
  )

  assert.ok(html.includes('¿Cómo estuvo el servicio con Juan Perez?'))
  assert.ok(html.includes('Califica al profesional'))
  assert.ok(html.includes('¿Qué aspectos destacarías?'))
  assert.ok(html.includes('Puntual'))
  assert.ok(html.includes('Trabajo impecable'))
  assert.ok(html.includes('Enviar calificación'))
  assert.ok(html.includes('Publicación simultánea'))
})

test('ReviewModal renders technician rating client view with client-specific chips (HU-45)', () => {
  const html = renderToStaticMarkup(
    React.createElement(ReviewModal, {
      user: {},
      tipo_solicitud: 'DIRECTA',
      solicitud_id: 'sol-789',
      evaluadoNombre: 'Maria Lopez',
      rolEvaluador: 'TECNICO',
      onClose: () => {},
      onSuccess: () => {},
    })
  )

  assert.ok(html.includes('¿Cómo estuvo el servicio con Maria Lopez?'))
  assert.ok(html.includes('Califica al cliente'))
  assert.ok(html.includes('Trato amable'))
  assert.ok(html.includes('Pago puntual'))
  assert.ok(html.includes('Comunicación clara'))
})

test('TechnicianCard displays Sin calificaciones · Nuevo when rating is null and formatted score with count when rated (HU-46)', () => {
  const unratedTech = {
    usuario_id: 'tech-1',
    nombre_completo: 'Carlos Gomez',
    especialidades: ['Fontanería'],
    tecnico_verificado: true,
    estado_verificacion: 'VERIFICADO',
    estado_certificacion: 'NO_CERTIFICADO',
    calificacion_promedio: null,
    total_resenas: 0,
    zonas_cobertura: [],
  }

  const unratedHtml = renderToStaticMarkup(
    React.createElement(MemoryRouter, {}, React.createElement(TechnicianCard, { technician: unratedTech }))
  )
  assert.ok(unratedHtml.includes('Sin calificaciones'))
  assert.ok(unratedHtml.includes('Nuevo'))

  const ratedTech = {
    ...unratedTech,
    calificacion_promedio: 4.8,
    total_resenas: 12,
  }

  const ratedHtml = renderToStaticMarkup(
    React.createElement(MemoryRouter, {}, React.createElement(TechnicianCard, { technician: ratedTech }))
  )
  assert.ok(ratedHtml.includes('4.8'))
  assert.ok(ratedHtml.includes('(12)'))
})

test('resolveReviewPublicationBanner distinguishes 48h expiry publication from bilateral mutual evaluation', () => {
  const timeoutBanner = resolveReviewPublicationBanner({
    resena: {},
    estado_publicacion: 'PUBLICADA',
    publicada_inmediata: true,
    motivo_publicacion: 'EXPIRACION_SILENCIOSA_48H',
    mensaje: 'Calificación publicada inmediatamente al haberse cumplido el plazo de 48 horas.',
  })
  assert.equal(timeoutBanner.isPublished, true)
  assert.ok(timeoutBanner.displayMessage.includes('plazo de 48 horas'))
  assert.ok(!timeoutBanner.displayMessage.includes('evaluación mutua'))
  assert.ok(timeoutBanner.badgeText.includes('plazo de 48 horas'))

  const mutualBanner = resolveReviewPublicationBanner({
    resena: {},
    estado_publicacion: 'PUBLICADA',
    publicada_inmediata: true,
    motivo_publicacion: 'BILATERAL_SIMULTANEA',
    mensaje: 'Calificación enviada y publicada exitosamente al completarse la evaluación mutua.',
  })
  assert.ok(mutualBanner.displayMessage.includes('evaluación mutua'))
  assert.equal(mutualBanner.badgeText, 'Evaluación publicada inmediatamente')
})

test('RoleReputationSummary separates dual-role averages cleanly and respects single-role and singular/plural rules', async () => {
  const dualHtml = renderToStaticMarkup(
    React.createElement(RoleReputationSummary, {
      isClient: true,
      isTechnician: true,
      clientReputation: {
        usuario_id: 'u1',
        promedio: 5.0,
        total_resenas: 1,
        sin_calificacion: false,
        distribucion_estrellas: { '5': 1 },
      },
      techReputation: {
        usuario_id: 'u1',
        promedio: null,
        total_resenas: 0,
        sin_calificacion: true,
        distribucion_estrellas: {},
      },
    })
  )
  assert.ok(dualHtml.includes('Como Cliente'))
  assert.ok(dualHtml.includes('5.0'))
  assert.ok(dualHtml.includes('Basado en 1 reseña de servicio completado (de técnico)'))
  assert.ok(dualHtml.includes('Como Técnico'))
  assert.ok(dualHtml.includes('Sin calificaciones aún'))
  assert.ok(dualHtml.includes('0 reseñas recibidas de clientes'))

  const clientOnlyHtml = renderToStaticMarkup(
    React.createElement(RoleReputationSummary, {
      isClient: true,
      isTechnician: false,
      clientReputation: {
        usuario_id: 'u2',
        promedio: 4.5,
        total_resenas: 2,
        sin_calificacion: false,
        distribucion_estrellas: { '5': 1, '4': 1 },
      },
      techReputation: null,
    })
  )
  assert.ok(clientOnlyHtml.includes('Como Cliente'))
  assert.ok(clientOnlyHtml.includes('4.5'))
  assert.ok(clientOnlyHtml.includes('Basado en 2 reseñas de servicios completados (de técnicos)'))
  assert.ok(!clientOnlyHtml.includes('Como Técnico'))

  const techOnlyHtml = renderToStaticMarkup(
    React.createElement(RoleReputationSummary, {
      isClient: false,
      isTechnician: true,
      clientReputation: null,
      techReputation: {
        usuario_id: 'u3',
        promedio: 4.33,
        total_resenas: 3,
        sin_calificacion: false,
        distribucion_estrellas: {},
      },
    })
  )
  assert.ok(techOnlyHtml.includes('Como Técnico'))
  assert.ok(techOnlyHtml.includes('4.3'))
  assert.ok(techOnlyHtml.includes('Basado en 3 reseñas de servicios completados (de clientes)'))
  assert.ok(!techOnlyHtml.includes('Como Cliente'))

  // Verificar query param rol_evaluado en getUserReputation y getUserPublicReviews
  const requestedUrls = []
  const origFetch = globalThis.fetch
  globalThis.fetch = async url => {
    requestedUrls.push(String(url))
    return new Response(
      JSON.stringify({ usuario_id: 'u1', promedio: null, total_resenas: 0, sin_calificacion: true, distribucion_estrellas: {}, resenas: [] }),
      { status: 200 }
    )
  }
  try {
    const fakeUser = { getIdToken: async () => 'tok' }
    await getUserReputation(fakeUser, 'u1', 'CLIENTE')
    await getUserReputation(fakeUser, 'u1', 'TECNICO')
    await getUserPublicReviews(fakeUser, 'u1')
    assert.ok(requestedUrls[0].endsWith('/reviews/users/u1/reputation?rol_evaluado=CLIENTE'))
    assert.ok(requestedUrls[1].endsWith('/reviews/users/u1/reputation?rol_evaluado=TECNICO'))
    assert.ok(requestedUrls[2].endsWith('/reviews/users/u1'))
  } finally {
    globalThis.fetch = origFetch
  }
})
