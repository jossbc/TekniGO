import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import {
  RefreshCw,
  X,
} from "lucide-react";
import { Button } from "../components/./ui";
import { errorMessage } from "../lib/api";
import {
  appendTicketActuationApi,
  getIncidentTicketDetailApi,
  listAdminIncidentInboxApi,
  resolveIncidentTicketApi,
  type IncidentTicket,
  type PrioridadTicket,
} from "../lib/incidents";

const PRIORITY_BADGES: Record<PrioridadTicket, { label: string; bg: string; color: string }> = {
  ALTA: { label: "Alta prioridad", bg: "#fef2f2", color: "#991b1b" },
  MEDIA: { label: "Prioridad media", bg: "#fffbeb", color: "#92400e" },
  NORMAL: { label: "Prioridad normal", bg: "#f1f5f9", color: "#475569" },
};

export default function AdminIncidentsManager({ user }: { user: User }) {
  const [tickets, setTickets] = useState<IncidentTicket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [filterEstado, setFilterEstado] = useState<string>("TODOS");
  const [filterPrioridad, setFilterPrioridad] = useState<string>("TODAS");

  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<IncidentTicket | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

    // Formulario de actuación intermedia (Audit Trail)
  const [actuationAction, setActuationAction] = useState("REVISION_DOCUMENTAL");
  const [actuationNotes, setActuationNotes] = useState("");
  const [savingActuation, setSavingActuation] = useState(false);
  const [actuationError, setActuationError] = useState("");

  // Formulario de resolución final (Strategy)
  const [decision, setDecision] = useState<
    "COMPLETAR_SERVICIO" | "DESBLOQUEAR_CONFIRMACION" | "CANCELAR_SERVICIO" | "SUSPENDER_USUARIO" | "CONCILIACION_REEMBOLSO"
  >("DESBLOQUEAR_CONFIRMACION");
  const [resolutionNotes, setResolutionNotes] = useState("");
  const [approveForceMajeure, setApproveForceMajeure] = useState(false);
  const [linkSuspension, setLinkSuspension] = useState(false);
  const [conciliateRefund, setConciliateRefund] = useState(false);
  const [submittingResolution, setSubmittingResolution] = useState(false);
  const [resolutionError, setResolutionError] = useState("");

  async function loadInbox() {
    setLoading(true);
    setError("");
    try {
      const data = await listAdminIncidentInboxApi(user, {
        estado: filterEstado === "TODOS" ? undefined : filterEstado,
        prioridad: filterPrioridad === "TODAS" ? undefined : filterPrioridad,
      });
      setTickets(data);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadInbox();
  }, [filterEstado, filterPrioridad]);

  async function openTicketDetail(ticketId: string) {
    setSelectedTicketId(ticketId);
    setLoadingDetail(true);
    setError("");
    setActuationError("");
    setResolutionError("");
    setActuationNotes("");
    setResolutionNotes("");
    setDecision("DESBLOQUEAR_CONFIRMACION");
    setLinkSuspension(false);
    setConciliateRefund(false);
    try {
      const detail = await getIncidentTicketDetailApi(user, ticketId);
      setSelectedDetail(detail);
      setApproveForceMajeure(detail.es_apelacion_justificada);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoadingDetail(false);
    }
  }

    async function handleAddActuation() {
    if (!selectedDetail || savingActuation) return;
    if (actuationNotes.trim().length < 5) {
      setActuationError("Las observaciones de la actuación deben tener al menos 5 caracteres.");
      return;
    }
    setSavingActuation(true);
    setActuationError("");
    try {
      await appendTicketActuationApi(user, selectedDetail.ticket_id, {
        accion: actuationAction,
        observaciones: actuationNotes.trim(),
      });
      const refreshed = await getIncidentTicketDetailApi(user, selectedDetail.ticket_id);
      setSelectedDetail(refreshed);
      setActuationNotes("");
      setNotice("Actuación registrada en el historial inmutable de auditoría.");
      loadInbox();
    } catch (err) {
      setActuationError(errorMessage(err));
    } finally {
      setSavingActuation(false);
    }
  }

  async function handleResolveTicket() {
    if (!selectedDetail || submittingResolution) return;
    if (resolutionNotes.trim().length < 5) {
      setResolutionError("El dictamen y observaciones debe tener al menos 5 caracteres.");
      return;
    }
    setSubmittingResolution(true);
    setResolutionError("");
    try {
      await resolveIncidentTicketApi(user, selectedDetail.ticket_id, {
        decision,
        observaciones: resolutionNotes.trim(),
        aprobar_justificacion_fuerza_mayor: approveForceMajeure,
        vincular_suspension: linkSuspension,
        conciliacion_reembolso: conciliateRefund,
      });
      const refreshed = await getIncidentTicketDetailApi(user, selectedDetail.ticket_id);
      setSelectedDetail(refreshed);
      setResolutionNotes("");
      setActuationNotes("");
      setLinkSuspension(false);
      setConciliateRefund(false);
      setNotice(`Ticket resuelto bajo la decisión: ${decision}.`);
      loadInbox();
    } catch (err) {
      setResolutionError(errorMessage(err));
    } finally {
      setSubmittingResolution(false);
    }
  }

  return (
    <section className="account-card" style={{ marginBlock: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h2>Bandeja de incidencias y moderación</h2>
          <p style={{ margin: 0, color: "#475569", fontSize: "14px" }}>
            Expediente prioritario para mediación de disputas, evidencias privadas y resoluciones inmutables.
          </p>
        </div>
        <Button className="button-outline" onClick={loadInbox} disabled={loading}>
          <RefreshCw size={16} /> Actualizar
        </Button>
      </div>

      {notice && <p className="success-alert" role="status">{notice}</p>}
      {error && <p className="alert" role="alert">{error}</p>}

      {/* Filtros de la bandeja */}
      <div className="admin-toolbar" style={{ flexWrap: "wrap", gap: "10px", marginBottom: "16px" }}>
        <div className="dossier-filters" role="group" aria-label="Filtrar por estado">
          {["TODOS", "ABIERTO", "EN_REVISION", "RESUELTO"].map((st) => (
            <Button
              key={st}
              className={filterEstado === st ? "" : "button-outline"}
              onClick={() => setFilterEstado(st)}
            >
              {st === "TODOS" ? "Todos los estados" : st.replace("_", " ")}
            </Button>
          ))}
        </div>
        <div className="dossier-filters" role="group" aria-label="Filtrar por prioridad">
          {["TODAS", "ALTA", "MEDIA", "NORMAL"].map((pr) => (
            <Button
              key={pr}
              className={filterPrioridad === pr ? "" : "button-outline"}
              onClick={() => setFilterPrioridad(pr)}
            >
              {pr === "TODAS" ? "Todas las prioridades" : pr}
            </Button>
          ))}
        </div>
      </div>

      {loading && <p>Cargando tickets de incidencia…</p>}

      {!loading && tickets.length === 0 && (
        <p style={{ padding: "20px", textAlign: "center", color: "#64748b" }}>
          No hay reportes de incidencias para los filtros seleccionados.
        </p>
      )}

      {/* Tabla priorizada de tickets (CA-01, CA-02) */}
      {!loading && tickets.length > 0 && (
        <div className="admin-table-wrap">
          <table className="admin-dossier-table">
            <thead>
              <tr>
                <th>Prioridad</th>
                <th>Tipo</th>
                <th>Reportante</th>
                <th>Servicio</th>
                <th>Fecha (Tegucigalpa)</th>
                <th>Estado</th>
                <th>Expediente</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((t) => {
                const badge = PRIORITY_BADGES[t.prioridad] || PRIORITY_BADGES.NORMAL;
                return (
                  <tr key={t.ticket_id} className={selectedTicketId === t.ticket_id ? "admin-dossier-selected" : ""}>
                    <td>
                      <span
                        style={{
                          background: badge.bg,
                          color: badge.color,
                          padding: "2px 8px",
                          borderRadius: "999px",
                          fontSize: "12px",
                          fontWeight: 700,
                        }}
                      >
                        {badge.label}
                      </span>
                    </td>
                    <td>
                      <strong>{t.tipo.replace(/_/g, " ")}</strong>
                      {t.es_disputa_cruzada && (
                          <div style={{ fontSize: "11px", background: "#fef3c7", color: "#92400e", padding: "2px 6px", borderRadius: "4px", marginTop: "4px", fontWeight: 700, width: "fit-content" }}>
                            Disputa bilateral (2 versiones)
                          </div>
                        )}
                      {t.es_apelacion_justificada && (
                        <div style={{ fontSize: "11px", color: "#7c3aed", fontWeight: 600 }}>
                          Apelación justificada
                        </div>
                      )}
                    </td>
                    <td>
                      {t.reportante_nombre} ({t.rol_reportante})
                    </td>
                    <td><code>{t.solicitud_id.slice(0, 8)}…</code></td>
                    <td>{t.creado_en_tegucigalpa}</td>
                    <td>
                      <span style={{ fontWeight: 600 }}>{t.estado}</span>
                    </td>
                    <td>
                      <Button onClick={() => openTicketDetail(t.ticket_id)}>
                        Ver expediente
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Modal del Expediente de Moderación Sensible (CA-02, CA-04, CA-05, CA-06, CA-07) */}
      {selectedTicketId && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Expediente de moderación"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.6)",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            zIndex: 100,
            padding: "20px",
          }}
        >
          <div
            style={{
              background: "#fff",
              borderRadius: "12px",
              maxWidth: "850px",
              width: "100%",
              maxHeight: "90vh",
              overflowY: "auto",
              padding: "24px",
              boxShadow: "0 20px 25px -5px rgba(0,0,0,0.2)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", borderBottom: "1px solid #e2e8f0", paddingBottom: "12px" }}>
              <div>
                <h3 style={{ margin: "0 0 4px" }}>Expediente de Moderación · Ticket {selectedTicketId.slice(0, 8)}</h3>
                <p style={{ margin: 0, fontSize: "13px", color: "#64748b" }}>
                  Solo administración autorizada tiene acceso a este expediente y evidencias sensibles.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedTicketId(null)}
                style={{ background: "none", border: "none", cursor: "pointer", padding: "4px" }}
              >
                <X size={20} />
              </button>
            </div>

            {loadingDetail && <p style={{ marginTop: 20 }}>Cargando expediente sensible…</p>}

            {selectedDetail && !loadingDetail && (
              <div style={{ marginTop: "16px" }}>
                {/* Resumen del caso */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", background: "#f8fafc", padding: "14px", borderRadius: "8px", fontSize: "14px" }}>
                  <div><strong>Solicitud ID:</strong> {selectedDetail.solicitud_id}</div>
                  <div><strong>Modalidad:</strong> {selectedDetail.modalidad}</div>
                  <div><strong>Reportante:</strong> {selectedDetail.reportante_nombre} ({selectedDetail.rol_reportante})</div>
                  <div><strong>Contraparte UID:</strong> <code>{selectedDetail.contraparte_uid}</code></div>
                  <div><strong>Estado del servicio al reportar:</strong> {selectedDetail.estado_servicio_al_reportar}</div>
                  <div><strong>Creado (Tegucigalpa):</strong> {selectedDetail.creado_en_tegucigalpa}</div>
                </div>

                                {/* Paso 3.2: Comparativa bilateral: Las dos caras de la moneda */}
                                {/* Comparativa bilateral y evidencias fotográficas */}
                {selectedDetail.es_disputa_cruzada ? (
                  <div style={{ marginTop: "16px", border: "2px solid #cbd5e1", borderRadius: "10px", padding: "16px", background: "#f8fafc" }}>
                    <h4 style={{ margin: "0 0 12px", color: "#0f172a" }}>
                      Disputa bilateral vinculada (ambas versiones en el mismo servicio)
                    </h4>

                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
                      {/* Lado del Cliente */}
                      <div style={{ background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: "8px", padding: "14px" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                          <strong style={{ color: "#1e40af" }}>Versión del Cliente</strong>
                          <span style={{ fontSize: "11px", color: "#64748b" }}>{selectedDetail.reporte_cliente?.creado_en_tegucigalpa}</span>
                        </div>
                        <div style={{ fontSize: "12px", color: "#1e3a8a", fontWeight: 600, marginBottom: "4px" }}>
                          Tipo: {selectedDetail.reporte_cliente?.tipo.replace(/_/g, " ")}
                        </div>
                        <p style={{ margin: 0, fontSize: "13px", color: "#1e293b", background: "#fff", padding: "8px 10px", borderRadius: "6px", border: "1px solid #dbeafe" }}>
                          {selectedDetail.reporte_cliente?.motivo}
                        </p>
                        <div style={{ marginTop: "10px", fontSize: "12px" }}>
                          <strong>Evidencias privadas:</strong>
                          {(!selectedDetail.reporte_cliente?.evidencias_privadas || selectedDetail.reporte_cliente.evidencias_privadas.length === 0) ? (
                            <span style={{ color: "#64748b" }}> Ninguna adjunta.</span>
                          ) : (
                            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "6px" }}>
                              {selectedDetail.reporte_cliente.evidencias_privadas.map((ev, i) => {
                                const isImg = ev.startsWith("data:image/") || /\.(jpg|jpeg|png|webp|gif)$/i.test(ev);
                                return isImg ? (
                                  <div key={i} style={{ border: "1px solid #cbd5e1", borderRadius: "6px", padding: "3px", background: "#fff" }}>
                                    <img
                                      src={ev}
                                      alt={`Evidencia cliente ${i + 1}`}
                                      style={{ width: "110px", height: "80px", objectFit: "cover", borderRadius: "4px", display: "block", cursor: "pointer" }}
                                      onClick={() => {
                                        const w = window.open("");
                                        w?.document.write(`<img src="${ev}" style="max-width:100%; height:auto;" />`);
                                      }}
                                    />
                                    <span style={{ fontSize: "10px", color: "#64748b", display: "block", textAlign: "center", marginTop: "2px" }}>Ver foto</span>
                                  </div>
                                ) : (
                                <div key={i} style={{ display: "flex", alignItems: "center", gap: "6px", background: "#fff", padding: "4px 8px", borderRadius: "4px", border: "1px solid #cbd5e1" }}>
                                    <code>{ev}</code>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Lado del Técnico */}
                      <div style={{ background: "#ffffff", border: "1px solid #cbd5e1", borderRadius: "8px", padding: "14px" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                          <strong style={{ color: "var(--ink, #18263b)" }}>Versión del Técnico</strong>
                          <span style={{ fontSize: "11px", color: "#64748b" }}>{selectedDetail.reporte_tecnico?.creado_en_tegucigalpa}</span>
                        </div>
                        <div style={{ fontSize: "12px", color: "#334155", fontWeight: 600, marginBottom: "4px" }}>
                          Tipo: {selectedDetail.reporte_tecnico?.tipo.replace(/_/g, " ")}
                          {selectedDetail.reporte_tecnico?.es_apelacion_justificada && (
                            <span style={{ marginLeft: "6px", background: "#eff6ff", color: "var(--blue, #2563eb)", padding: "1px 6px", borderRadius: "4px", fontSize: "11px" }}>
                              Apelación justificada
                            </span>
                          )}
                        </div>
                        <p style={{ margin: 0, fontSize: "13px", color: "#1e293b", background: "#f8fafc", padding: "8px 10px", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
                          {selectedDetail.reporte_tecnico?.motivo}
                        </p>
                        <div style={{ marginTop: "10px", fontSize: "12px" }}>
                          <strong>Evidencias privadas:</strong>
                          {(!selectedDetail.reporte_tecnico?.evidencias_privadas || selectedDetail.reporte_tecnico.evidencias_privadas.length === 0) ? (
                            <span style={{ color: "#64748b" }}> Ninguna adjunta.</span>
                          ) : (
                            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "6px" }}>
                              {selectedDetail.reporte_tecnico.evidencias_privadas.map((ev, i) => {
                                const isImg = ev.startsWith("data:image/") || /\.(jpg|jpeg|png|webp|gif)$/i.test(ev);
                                return isImg ? (
                                  <div key={i} style={{ border: "1px solid #cbd5e1", borderRadius: "6px", padding: "3px", background: "#fff" }}>
                                    <img
                                      src={ev}
                                      alt={`Evidencia técnico ${i + 1}`}
                                      style={{ width: "110px", height: "80px", objectFit: "cover", borderRadius: "4px", display: "block", cursor: "pointer" }}
                                      onClick={() => {
                                        const w = window.open("");
                                        w?.document.write(`<img src="${ev}" style="max-width:100%; height:auto;" />`);
                                      }}
                                    />
                                    <span style={{ fontSize: "10px", color: "#64748b", display: "block", textAlign: "center", marginTop: "2px" }}>Ver foto</span>
                                  </div>
                                ) : (
                                <div key={i} style={{ display: "flex", alignItems: "center", gap: "6px", background: "#fff", padding: "4px 8px", borderRadius: "4px", border: "1px solid #cbd5e1" }}>
                                    <code>{ev}</code>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  /* Si solo una parte ha reportado */
                  <div style={{ marginTop: "14px" }}>
                    <h4 style={{ margin: "0 0 6px" }}>Motivo del reporte ({selectedDetail.rol_reportante}):</h4>
                    <p style={{ margin: 0, background: "#fef2f2", borderLeft: "4px solid #ef4444", padding: "10px 14px", borderRadius: "4px" }}>
                      {selectedDetail.motivo}
                    </p>
                    <div style={{ marginTop: "10px", fontSize: "12px" }}>
                      <strong>Evidencias privadas:</strong>
                      {(!selectedDetail.evidencias_privadas || selectedDetail.evidencias_privadas.length === 0) ? (
                        <span style={{ color: "#64748b" }}> Ninguna adjunta.</span>
                      ) : (
                        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "6px" }}>
                          {selectedDetail.evidencias_privadas.map((ev, i) => {
                            const isImg = ev.startsWith("data:image/") || /\.(jpg|jpeg|png|webp|gif)$/i.test(ev);
                            return isImg ? (
                              <div key={i} style={{ border: "1px solid #cbd5e1", borderRadius: "6px", padding: "3px", background: "#fff" }}>
                                <img
                                  src={ev}
                                  alt={`Evidencia ${i + 1}`}
                                  style={{ width: "110px", height: "80px", objectFit: "cover", borderRadius: "4px", display: "block", cursor: "pointer" }}
                                  onClick={() => {
                                    const w = window.open("");
                                    w?.document.write(`<img src="${ev}" style="max-width:100%; height:auto;" />`);
                                  }}
                                />
                                <span style={{ fontSize: "10px", color: "#64748b", display: "block", textAlign: "center", marginTop: "2px" }}>Ver foto</span>
                              </div>
                            ) : (
                                <div key={i} style={{ display: "flex", alignItems: "center", gap: "6px", background: "#fff", padding: "4px 8px", borderRadius: "4px", border: "1px solid #cbd5e1" }}>
                                    <code>{ev}</code>
                                  </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                    <p style={{ fontSize: "12px", color: "#64748b", marginTop: "8px" }}>
                      La contraparte ({selectedDetail.rol_reportante === "CLIENTE" ? "Técnico" : "Cliente"}) aún no ha registrado un reporte o descargo formal sobre este servicio.
                    </p>
                  </div>
                )}

                {/* Historial de Actuaciones (Patrón Memento / Audit Trail - CA-04) */}
                <div style={{ marginTop: "20px" }}>
                  <h4 style={{ margin: "0 0 8px" }}>Historial inmutable de actuaciones:</h4>
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                    {selectedDetail.historial_actuaciones.map((act) => (
                      <div
                        key={act.actuacion_id}
                        style={{
                          border: "1px solid #e2e8f0",
                          borderRadius: "6px",
                          padding: "10px 12px",
                          fontSize: "13px",
                          background: "#ffffff",
                        }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600, color: "#1e293b" }}>
                          <span>[{act.autor_rol}] {act.accion}</span>
                          <span style={{ fontSize: "12px", color: "#64748b" }}>{act.instante_tegucigalpa}</span>
                        </div>
                        <p style={{ margin: "4px 0 0", color: "#334155" }}>{act.observaciones}</p>
                      </div>
                    ))}
                  </div>

                  {/* Formulario para apendizar actuación intermedia */}
                  {selectedDetail.estado !== "RESUELTO" && (
                                        <div style={{ marginTop: "12px", background: "#f1f5f9", padding: "12px", borderRadius: "8px" }}>
                      <div style={{ fontWeight: 600, marginBottom: "8px", fontSize: "13px" }}>
                        Añadir actuación intermedia (mínimo 5 caracteres):
                      </div>
                      {actuationError && (
                        <p className="alert" role="alert" style={{ margin: "0 0 8px", fontSize: "13px" }}>
                          {actuationError}
                        </p>
                      )}
                      <div style={{ display: "flex", gap: "8px", marginBottom: "8px" }}>
                        <select
                          value={actuationAction}
                          onChange={(e) => setActuationAction(e.target.value)}
                          style={{ padding: "6px 10px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                        >
                          <option value="REVISION_DOCUMENTAL">REVISION DOCUMENTAL</option>
                          <option value="SOLICITUD_DESCARGOS">SOLICITUD DESCARGOS</option>
                          <option value="INSPECCION_TECNICA">INSPECCION TECNICA</option>
                          <option value="CONCILIACION_TELEFONICA">CONCILIACION TELEFONICA</option>
                        </select>
                        <input
                          type="text"
                          placeholder="Observaciones de la actuación..."
                          value={actuationNotes}
                          onChange={(e) => {
                            setActuationNotes(e.target.value);
                            if (actuationError) setActuationError("");
                          }}
                          style={{ flex: 1, padding: "6px 10px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                        />
                      </div>
                      <Button
                        type="button"
                        onClick={handleAddActuation}
                        disabled={savingActuation || !actuationNotes.trim()}
                      >
                        Registrar actuación
                      </Button>
                    </div>
                  )}
                </div>

                {/* Formulario de Resolución Final (Patrón Strategy - CA-04, CA-05, CA-06, CA-07) */}
                {selectedDetail.estado !== "RESUELTO" ? (
                  <div style={{ marginTop: "24px", borderTop: "2px solid #e2e8f0", paddingTop: "16px" }}>
                    <h4 style={{ margin: "0 0 10px", color: "#0f172a" }}>
                      Emitir resolución administrativa formal
                    </h4>
                    <div style={{ display: "grid", gap: "10px" }}>
                      <div>
                        <label style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                          Decisión a ejecutar:
                        </label>
                        <select
                          value={decision}
                          onChange={(e) => setDecision(e.target.value as any)}
                          style={{ width: "100%", padding: "8px 12px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                        >
                          <option value="DESBLOQUEAR_CONFIRMACION">DESBLOQUEAR CONFIRMACIÓN (Permitir continuar cierre o reprogramar)</option>
                          <option value="COMPLETAR_SERVICIO">COMPLETAR SERVICIO (Validar trabajo y liberar fondos)</option>
                          <option value="CANCELAR_SERVICIO">CANCELAR SERVICIO (Reversión sin penalización indebida)</option>
                          <option value="SUSPENDER_USUARIO">SUSPENDER USUARIO (Sanción por riesgo grave o dolo)</option>
                          <option value="CONCILIACION_REEMBOLSO">CONCILIACION DE REEMBOLSO (Acuerdo sin declarar pagos no confirmados)</option>
                        </select>
                      </div>

                      <div>
                        <label style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                          Dictamen y observaciones de la resolución (mínimo 5 caracteres):
                        </label>
                        {resolutionError && (
                          <p className="alert" role="alert" style={{ margin: "0 0 8px", fontSize: "13px" }}>
                            {resolutionError}
                          </p>
                        )}
                        <textarea
                          rows={3}
                          value={resolutionNotes}
                          onChange={(e) => {
                            setResolutionNotes(e.target.value);
                            if (resolutionError) setResolutionError("");
                          }}
                          placeholder="Fundamento de la resolución, responsable y efectos aplicados..."
                          style={{ width: "100%", padding: "8px 12px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                        />
                      </div>

                      {/* Checkboxes de condiciones especiales (CA-05 y CA-07) */}
                      <div style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "13px", background: "#f8fafc", padding: "10px", borderRadius: "6px" }}>
                        <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                          <input
                            type="checkbox"
                            checked={approveForceMajeure}
                            onChange={(e) => setApproveForceMajeure(e.target.checked)}
                          />
                          <span>Aprobar justificación por fuerza mayor / conducta de contraparte (excluye la falta del cómputo).</span>
                        </label>
                        <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                          <input
                            type="checkbox"
                            checked={linkSuspension}
                            onChange={(e) => setLinkSuspension(e.target.checked)}
                          />
                          <span>Vincular a suspensión administrativa del usuario denunciado.</span>
                        </label>
                        <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                          <input
                            type="checkbox"
                            checked={conciliateRefund}
                            onChange={(e) => setConciliateRefund(e.target.checked)}
                          />
                          <span>Registrar conciliación de reembolso (sin declarar pagos bancarios no confirmados).</span>
                        </label>
                      </div>

                                          <Button
                        type="button"
                        onClick={handleResolveTicket}
                        disabled={submittingResolution || !resolutionNotes.trim()}
                        style={{ background: "var(--blue)", borderColor: "var(--blue)", color: "#fff", marginTop: "6px" }}
                      >
                        {submittingResolution ? "Aplicando resolución…" : "Firmar y aplicar resolución formal"}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div style={{ marginTop: "20px", background: "#f0fdf4", border: "1px solid #bbf7d0", padding: "14px", borderRadius: "8px", fontSize: "14px" }}>
                    <div style={{ fontWeight: 700, color: "#166534", marginBottom: "4px" }}>
                      Ticket resuelto bajo dictamen: {selectedDetail.resolucion_final?.decision}
                    </div>
                    <div><strong>Fecha:</strong> {selectedDetail.resolucion_final?.resuelto_en_tegucigalpa}</div>
                    <div><strong>Observaciones:</strong> {selectedDetail.resolucion_final?.observaciones}</div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}