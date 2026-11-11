import { useEffect, useState } from 'react'
import type { User } from 'firebase/auth'
import { FolderOpen } from 'lucide-react'
import Header from '../components/Header'
import { AccountStatus } from '../components/AccountStatus'
import { Button } from '../components/ui'
import { useAccount } from '../lib/useAccount'
import { api, errorMessage } from '../lib/api'
import AdminCertificates from '../components/AdminCertificates'
import DocumentReviewCard from '../components/DocumentReviewCard'
import DossierModal from '../components/DossierModal'
import type { AdminDossier, DossierList, ReviewOutcome } from '../lib/adminVerification'

const labels: Record<string, string> = {
  DNI_FRONTAL: 'DNI frontal', DNI_REVERSO: 'DNI reverso', SELFIE: 'Selfie',
  ANTECEDENTES_POLICIALES: 'Antecedentes policiales', ANTECEDENTES_PENALES: 'Antecedentes penales',
}

export default function AdminPage({ user }: { user: User }) {
  const { state, retry } = useAccount(user)
  return <div className="site">
    <Header authenticated email={user.email} accountHref="/admin" accountLabel="Administración" />
    <main className="account admin-panel">
      <div className="admin-page-heading">
        <h1>Revisión de expedientes</h1>
      </div>
      {state.status !== 'ready' ? <AccountStatus state={state} retry={retry} /> :
        !state.profile.roles.includes('ADMIN') ? <p className="alert" role="alert">Acceso denegado. Esta sección requiere una cuenta administradora.</p> : <DossierManager user={user} />}
    </main>
  </div>
}

function RoleBadges({ roles }: { roles: string[] }) {
  return <span className="dossier-role-badges">{roles.map(role => <span key={role} className={`dossier-role dossier-role-${role.toLowerCase()}`}>{role === 'CLIENTE' ? 'Cliente' : 'Técnico'}</span>)}</span>
}

function DossierManager({ user }: { user: User }) {
  const [list, setList] = useState<DossierList | null>(null)
  const [offset, setOffset] = useState(0)
  const [type, setType] = useState('TODOS')
  const [selected, setSelected] = useState('')
  const [dossier, setDossier] = useState<AdminDossier | null>(null)
  const [error, setError] = useState('')
  const [dossierError, setDossierError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [certificateRefresh, setCertificateRefresh] = useState(0)
  const [notice, setNotice] = useState('')
  function handleSaved(result: ReviewOutcome) {
    if (result.revision_completa) {
      setSelected('')
      setNotice('Expediente verificado. La revisión se completó y salió de pendientes.')
    }
    setRefresh(value => value + 1)
  }
  const [processing, setProcessing] = useState(false)
  async function evaluateIdentity() {
    if (processing || !dossier?.solicitud_id) return
    setProcessing(true); setError('')
    try {
      await api(`/admin/dossiers/${dossier.usuario_id}/identity-verification`, user, { solicitud_id: dossier.solicitud_id })
      setRefresh(value => value + 1)
    } catch (err) { setError(errorMessage(err)) }
    finally { setProcessing(false) }
  }
  useEffect(() => {
    let active = true
    setError('')
    api<DossierList>(`/admin/dossiers?offset=${offset}&limit=20&tipo=${type}`, user)
      .then(value => { if (active) { setList(value); if (!value.expedientes.length && offset > 0) setOffset(Math.max(0, offset - 20)) } })
      .catch(err => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [user, offset, refresh, certificateRefresh, type])
  useEffect(() => {
    let active = true
    setDossier(null); setDossierError('')
    if (selected) api<AdminDossier>(`/admin/dossiers/${selected}/dossier`, user)
      .then(value => { if (active) setDossier(value) })
      .catch(err => { if (active) setDossierError(errorMessage(err)) })
    return () => { active = false }
  }, [user, selected, refresh])
  return <>
    {notice && <p className="success-alert" role="status">{notice}</p>}
    {error && !selected && <p className="alert" role="alert">{error}</p>}
    <p>Los clientes se verifican automáticamente. Aquí se revisan los casos que requieren intervención administrativa y los expedientes técnicos.</p>
    <div className="admin-toolbar">
      <div className="dossier-filters" role="group" aria-label="Filtrar por tipo de cuenta">
        {[['TODOS', 'Todos'], ['CLIENTE', 'Clientes'], ['TECNICO', 'Técnicos'], ['CERTIFICACIONES', 'Con certificaciones']].map(([value, label]) => <Button key={value} className={type === value ? '' : 'button-outline'} aria-pressed={type === value} onClick={() => { if (type !== value) { setType(value); setOffset(0); setList(null) } }}>{label}</Button>)}
      </div>
      <Button className="button-outline" onClick={() => setRefresh(value => value + 1)}>Actualizar expedientes</Button>
    </div>
    {!list && !error && <p role="status">Cargando expedientes…</p>}
    {list && <section className="account-card" style={{ marginBlock: 24 }}>
      <h2>{type === 'CERTIFICACIONES' ? 'Expedientes con certificaciones' : 'Expedientes pendientes'} ({list.total})</h2>
      {list.expedientes.length === 0 && <p>No hay expedientes para este filtro.</p>}
      <div className="admin-table-wrap"><table className="admin-dossier-table">
        <thead><tr><th>Nombre</th><th>Tipo de cuenta</th><th>Identidad</th><th>Expediente</th></tr></thead>
        <tbody>{list.expedientes.map(item => <tr key={item.usuario_id} className={selected === item.usuario_id ? 'admin-dossier-selected' : ''}>
          <td>{item.nombre_completo}</td><td><RoleBadges roles={item.roles} /></td><td>{item.estado_identidad.replaceAll('_', ' ')}</td>
          <td><Button aria-label={`Abrir expediente de ${item.nombre_completo}`} aria-pressed={selected === item.usuario_id} onClick={() => { setNotice(''); setSelected(item.usuario_id) }}><FolderOpen size={16} />Abrir</Button></td>
        </tr>)}</tbody>
      </table></div>
      <div className="admin-toolbar admin-pagination">
        <Button className="button-outline" disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 20))}>Anterior</Button>
        <Button className="button-outline" disabled={offset + 20 >= list.total} onClick={() => setOffset(value => value + 20)}>Siguiente</Button>
      </div>
    </section>}
    {selected && <DossierModal onClose={() => setSelected('')}>
      {error && <p className="alert" role="alert">{error}</p>}
      {dossierError && <p className="alert" role="alert">No pudimos abrir el expediente: {dossierError}</p>}
      {selected && !dossier && !dossierError && <p role="status">Cargando expediente seleccionado…</p>}
      {dossier?.usuario_id === selected && <section aria-label="Expediente seleccionado">
        <h2>{dossier.nombre_completo} · Versión {dossier.version ?? 'sin documentos'}</h2>
        <RoleBadges roles={dossier.roles} />
        <p>DNI registrado: {dossier.dni}</p>
        <p>Teléfono: {dossier.telefono} · {dossier.telefono_verificado ? 'Verificado' : 'Pendiente de verificación'}</p>
        <p>Identidad: {dossier.estado_identidad}{dossier.estado_verificacion && ` · Revisión técnica: ${dossier.estado_verificacion}`}</p>
        <p>Las decisiones documentales se evalúan junto con los requisitos de habilitación. Las suspensiones y demás restricciones se mantienen.</p>
        {dossier.estado_identidad === 'VERIFICADO' && <p className="success-alert" role="status">Identidad verificada: DNI y selfie aprobados.</p>}
        {dossier.roles.includes('TECNICO') && dossier.estado_verificacion !== 'VERIFICADO' && <div>
          <h3>Requisitos pendientes de verificación técnica</h3>
          <ul>{Object.entries(labels).filter(([type]) => dossier.documentos.find(doc => doc.tipo === type)?.estado !== 'APROBADO').map(([type, label]) => <li key={type}>{label}: {dossier.documentos.find(doc => doc.tipo === type)?.estado || 'Faltante'}</li>)}
            {!dossier.telefono_verificado && <li>Teléfono pendiente de verificación por el titular.</li>}
          </ul>
        </div>}
        <h3>Evaluación de identidad</h3>
        {dossier.solicitud_id && !dossier.decisiones.some(decision => decision.version === dossier.version) &&
          <Button type="button" style={{ width: 'auto' }} disabled={processing} onClick={evaluateIdentity}>{processing ? 'Evaluando identidad…' : 'Evaluar DNI y selfie'}</Button>}
        {!dossier.resultados_identidad.length && <p>Sin resultado de procesamiento. Comprueba los datos del DNI y la correspondencia del rostro con la selfie durante la revisión.</p>}
        <details><summary>Historial de evaluaciones (incluye resultados anteriores)</summary>{dossier.resultados_identidad.map((result, index) => <p key={index}>{result.estado}: {result.motivo}</p>)}</details>
        {Object.entries(labels).filter(([type]) => dossier.roles.includes('TECNICO') || ['DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE'].includes(type)).map(([type, label]) => {
          const doc = dossier.documentos.find(item => item.tipo === type)
          return doc ? <DocumentReviewCard key={`${doc.id}-${refresh}`} user={user} userId={dossier.usuario_id} doc={doc} label={label} machineRejected={dossier.resultados_identidad.find(result => result.proceso_responsable === 'PROCESAMIENTO_IDENTIDAD')?.estado === 'RECHAZADO'} identity={['DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE'].includes(type)} onSaved={handleSaved} /> :
            <p className="alert" key={type}>{label}: faltante. Impide completar favorablemente la revisión.</p>
        })}
        {dossier.roles.includes('TECNICO') && <AdminCertificates key={dossier.usuario_id} user={user} userId={dossier.usuario_id} onSaved={() => setCertificateRefresh(value => value + 1)} />}
        <h3>Historial privado de decisiones</h3>
        {!dossier.decisiones.length && <p>Aún no hay decisiones administrativas.</p>}
        <ul>{dossier.decisiones.map((decision, index) => <li key={index}>
          Versión {decision.version} · Documento {decision.documento_id} · {decision.estado} · {new Date(decision.fecha).toLocaleString('es-HN')} · Administrador {decision.administrador_id}
          {decision.observaciones && <p>{decision.observaciones}</p>}
        </li>)}</ul>
      </section>}
    </DossierModal>}
  </>
}
