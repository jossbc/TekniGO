import { useEffect, useState, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { Download } from 'lucide-react'
import { api, errorMessage } from '../lib/api'
import { Button } from './ui'
import './AdminReports.css'

type Filters = Record<'desde' | 'hasta' | 'oficio' | 'departamento' | 'municipio', string>
type Rank = { nombre: string; solicitudes: number }
type Weekday = Rank & { dias: number; promedio: number | null }
type Report = {
  filtros: Pick<Filters, 'desde' | 'hasta'> & Record<'oficio' | 'departamento' | 'municipio', string | null>
  opciones: { oficios: { id: string; nombre: string }[]; ubicaciones: Record<string, string[]> }
  resumen: Record<'total' | 'directas' | 'abiertas' | 'contratadas' | 'completadas', number>
  oficios: Rank[]
  departamentos: Rank[]
  municipios: Rank[]
  dias: { fecha: string; solicitudes: number }[]
  semana: Weekday[]
  destacados: { oficios: Rank[]; municipios: Rank[]; semana: Weekday[] }
}

const emptyFilters: Filters = { desde: '', hasta: '', oficio: '', departamento: '', municipio: '' }
const number = new Intl.NumberFormat('es-HN')
const percent = new Intl.NumberFormat('es-HN', { style: 'percent', maximumFractionDigits: 1 })
const dateLabel = new Intl.DateTimeFormat('es-HN', { dateStyle: 'medium', timeZone: 'UTC' })
const summaryLabels = {
  total: 'Solicitudes', directas: 'Directas', abiertas: 'Abiertas',
  contratadas: 'Contratadas', completadas: 'Completadas',
} as const

function localDate(value: string) {
  return dateLabel.format(new Date(`${value}T00:00:00Z`))
}

function appliedFilters(report: Report): Filters {
  const { desde, hasta, oficio, departamento, municipio } = report.filtros
  return {
    desde, hasta, oficio: oficio ?? '',
    departamento: departamento ?? '', municipio: municipio ?? '',
  }
}

export function demandReportCsv(report: Report) {
  const filters = appliedFilters(report)
  const trade = report.opciones.oficios.find(item => item.id === filters.oficio)?.nombre
  const rows: (string | number)[][] = [
    ['Sección', 'Concepto', 'Solicitudes o valor', 'Proporción del total', 'Días observados', 'Promedio diario'],
    ['Filtro', 'Desde', filters.desde],
    ['Filtro', 'Hasta', filters.hasta],
    ['Filtro', 'Oficio', trade ?? 'Todos'],
    ['Filtro', 'Departamento', filters.departamento || 'Todos'],
    ['Filtro', 'Municipio', filters.municipio || 'Todos'],
    ...Object.entries(summaryLabels).map(([key, label]) => [
      'Resumen', label, report.resumen[key as keyof Report['resumen']],
    ]),
    ...([
      ['Oficio', report.oficios], ['Departamento', report.departamentos], ['Municipio', report.municipios],
    ] as const).flatMap(([section, items]) => items.map(item => [
      section, item.nombre, item.solicitudes, percent.format(item.solicitudes / report.resumen.total),
    ])),
    ...report.dias.map(day => ['Fecha', day.fecha, day.solicitudes]),
    ...report.semana.map(day => ['Día de la semana', day.nombre, day.solicitudes, '', day.dias, day.promedio ?? '']),
  ]
  return rows.map(row => Array.from({ length: 6 }, (_, index) => {
    const value = row[index] ?? ''
    if (typeof value === 'number') return String(value)
    // Spreadsheet applications can evaluate formula prefixes even inside quoted cells.
    const safe = /^\s*[=+\-@]|^[\t\r\n]/u.test(value) ? `'${value}` : value
    return `"${safe.replaceAll('"', '""')}"`
  }).join(',')).join('\r\n')
}

function exportReport(report: Report) {
  const { desde, hasta } = report.filtros
  const url = URL.createObjectURL(new Blob(['\uFEFF', demandReportCsv(report)], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `teknigo-demanda-${desde}-${hasta}.csv`
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function Ranking({ title, items, total }: { title: string; items: Rank[]; total: number }) {
  const peak = items[0]?.solicitudes || 1
  const [expanded, setExpanded] = useState(false)
  return <section className="account-card demand-card">
    <h3>{title}</h3>
    <ol className="demand-rank">
      {(expanded ? items : items.slice(0, 8)).map(item => <li key={item.nombre}>
        <div><span>{item.nombre}</span><strong>{number.format(item.solicitudes)} · {percent.format(item.solicitudes / total)}</strong></div>
        <meter aria-label={item.nombre} min={0} max={peak} value={item.solicitudes} />
      </li>)}
    </ol>
    {items.length > 8 && <Button type="button" className="button-outline demand-expand" onClick={() => setExpanded(!expanded)}>
      {expanded ? 'Mostrar menos' : 'Ver todos'}
    </Button>}
  </section>
}

export default function AdminReports({ user }: { user: User }) {
  const [filters, setFilters] = useState<Filters>(emptyFilters)
  const [search, setSearch] = useState<Filters>(emptyFilters)
  const [report, setReport] = useState<Report | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    const query = new URLSearchParams(Object.entries(search).filter(([, value]) => value))
    api<Report>(`/admin/reports/demand?${query}`, user)
      .then(result => {
        if (!active) return
        setReport(result)
        setFilters(appliedFilters(result))
      })
      .catch(err => { if (active) setError(errorMessage(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [user, search, attempt])

  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSearch({ ...filters })
  }

  const locations = report?.opciones.ubicaciones ?? {}
  const cities = [...new Set(filters.departamento ? locations[filters.departamento] ?? [] : Object.values(locations).flat())]
    .sort((a, b) => a.localeCompare(b, 'es'))
  const peak = Math.max(1, ...(report?.dias.map(day => day.solicitudes) ?? []))
  const weekPeak = Math.max(1, ...(report?.semana.map(day => day.promedio ?? 0) ?? []))
  const committedFilters = report && appliedFilters(report)
  const canExport = committedFilters && !loading && !error &&
    Object.entries(committedFilters).every(([key, value]) => filters[key as keyof Filters] === value)

  return <section className="demand-dashboard" aria-labelledby="demand-title">
    <div className="demand-heading">
      <div><h2 id="demand-title">Demanda de servicios</h2><p>Solicitudes directas enviadas y solicitudes abiertas publicadas.</p></div>
      <Button type="button" className="button-outline" disabled={!canExport} onClick={() => report && exportReport(report)}>
        <Download size={17} /> Exportar CSV
      </Button>
    </div>
    <form className="account-card demand-filters" onSubmit={apply} aria-label="Filtros del reporte">
      <label className="field">Desde<input type="date" required value={filters.desde} max={filters.hasta || undefined} onChange={event => setFilters({ ...filters, desde: event.target.value })} /></label>
      <label className="field">Hasta<input type="date" required value={filters.hasta} min={filters.desde || undefined} onChange={event => setFilters({ ...filters, hasta: event.target.value })} /></label>
      <label className="field">Oficio<select value={filters.oficio} onChange={event => setFilters({ ...filters, oficio: event.target.value })}>
        <option value="">Todos</option>
        {report?.opciones.oficios.map(trade => <option key={trade.id} value={trade.id}>{trade.nombre}</option>)}
      </select></label>
      <label className="field">Departamento<select value={filters.departamento} onChange={event => setFilters({ ...filters, departamento: event.target.value, municipio: '' })}>
        <option value="">Todos</option>
        {Object.keys(locations).sort((a, b) => a.localeCompare(b, 'es')).map(place => <option key={place}>{place}</option>)}
      </select></label>
      <label className="field">Municipio<select value={filters.municipio} onChange={event => setFilters({ ...filters, municipio: event.target.value })}>
        <option value="">Todos</option>
        {cities.map(city => <option key={city}>{city}</option>)}
      </select></label>
      <Button type="submit" disabled={loading}>Aplicar filtros</Button>
    </form>
    {loading && <p role="status">Cargando reporte de demanda…</p>}
    {error && <div className="alert" role="alert">
      <p>{error}</p><Button type="button" className="button-outline" onClick={() => setAttempt(value => value + 1)}>Reintentar</Button>
    </div>}
    {!loading && !error && report && <>
      <p className="demand-period">{localDate(report.filtros.desde)} al {localDate(report.filtros.hasta)} · Hora de Honduras</p>
      <dl className="demand-kpis">
        {Object.entries(summaryLabels).map(([key, label]) => <div className="account-card" key={key}>
          <dt>{label}</dt><dd>{number.format(report.resumen[key as keyof Report['resumen']])}</dd>
        </div>)}
      </dl>
      {report.resumen.total === 0 ? <p className="account-card" role="status">No se encontraron solicitudes con estos filtros.</p> : <>
        <section className="account-card demand-insights">
          <h3>Hallazgos del período</h3>
          <dl>
            {([
              ['Oficio con más solicitudes', report.destacados.oficios],
              ['Zona con más solicitudes', report.destacados.municipios],
            ] as const).map(([label, items]) => <div key={label}><dt>{label}</dt>
              <dd>{items.map(item => item.nombre).join(', ')} · {number.format(items[0].solicitudes)}{items.length > 1 && ' por cada uno (empate)'}</dd>
            </div>)}
            <div><dt>Día con mayor demanda promedio</dt><dd>{report.destacados.semana.map(day => day.nombre).join(', ')} · {number.format(report.destacados.semana[0].promedio ?? 0)}{report.destacados.semana.length > 1 && ' por día (empate)'}</dd></div>
          </dl>
        </section>
        <div className="demand-charts">
          <Ranking title="Demanda por oficio" items={report.oficios} total={report.resumen.total} />
          <Ranking title="Demanda por departamento" items={report.departamentos} total={report.resumen.total} />
          <Ranking title="Demanda por municipio" items={report.municipios} total={report.resumen.total} />
          <section className="account-card demand-card">
            <h3>Promedio por día de la semana</h3>
            <ul className="demand-rank">
              {report.semana.map(day => <li key={day.nombre}>
                <div><span>{day.nombre}</span><strong>{day.promedio === null ? 'Fuera del período' : number.format(day.promedio)}</strong></div>
                {day.promedio !== null && <meter aria-label={`Promedio del ${day.nombre.toLowerCase()}`} min={0} max={weekPeak} value={day.promedio} />}
              </li>)}
            </ul>
          </section>
        </div>
        <section className="account-card demand-card">
          <h3>Solicitudes por fecha</h3>
          <div className="demand-daily" role="img" aria-label="Gráfica de solicitudes por fecha. Los valores completos están en la tabla inferior.">
            {report.dias.map(day => <div key={day.fecha} title={`${localDate(day.fecha)} · ${number.format(day.solicitudes)} solicitudes`}>
              <span style={{ height: `${day.solicitudes / peak * 100}%` }} />
            </div>)}
          </div>
          <div className="demand-daily-labels"><span>{localDate(report.filtros.desde)}</span><span>{localDate(report.filtros.hasta)}</span></div>
          <details><summary>Ver tabla por fecha</summary>
            <table><thead><tr><th scope="col">Fecha</th><th scope="col">Solicitudes</th></tr></thead>
              <tbody>{report.dias.map(day => <tr key={day.fecha}><td>{localDate(day.fecha)}</td><td>{number.format(day.solicitudes)}</td></tr>)}</tbody>
            </table>
          </details>
        </section>
      </>}
      <p className="demand-note">El período se aplica a la fecha de creación o publicación. Contratadas incluye las solicitudes que llegaron a un acuerdo, aunque se cancelaran después. Una reapertura publicada se cuenta como una nueva licitación.</p>
    </>}
  </section>
}
