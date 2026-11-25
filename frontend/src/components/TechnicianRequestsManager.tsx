import RequestTimeline from "./RequestTimeline";
import { getOpportunityDetailApi, type TechnicianOpportunityItem } from "../lib/openRequests";
import { useState, useEffect } from "react";
import type { User } from "firebase/auth";
import { CheckCircle2, CircleAlert, Clock3, XCircle, Calendar, Inbox, Check, X, RefreshCw, Star } from "lucide-react";
import { errorMessage } from "../lib/api";
import {
  acceptTechnicianDirectRequest,
  getTechnicianDirectRequest,
  formatConfirmationWindowStatus,
  formatTegucigalpaDateTime,
  listTechnicianDirectRequests,
  rejectTechnicianDirectRequest,
  reportTechnicianRequestCompletion,
  startTechnicianDirectRequest,
  type TechnicianDirectRequest,
  type TechnicianDirectRequestDetail,
} from "../lib/directRequests";
import LoadingState from "./LoadingState";
import ReviewModal from "./ReviewModal";
import CancellationNotice from "./CancellationNotice";
import AdjustmentPanel from "./AdjustmentPanel";
import { CancelRequestModal } from "./CancellationForms";
import { Button } from "./ui";
import StartServiceConfirmation from "./StartServiceConfirmation";
import ServiceLocationContactCard from "../lib/ServiceLocationContactCard";
import { AlertTriangle, MessageSquare } from "lucide-react";
import ReportIncidentModal from "./ReportIncidentModal";
import ContractChatPanel from "./ContractChatPanel";
import { isChatEnabledForState } from "../lib/contractChat";
import { subscribeGeneralNotificationLiveUpdate, subscribeProposalLiveUpdate } from "../lib/notifications";

export type DirectRequest = TechnicianDirectRequest;

type EstadoFilter = "TODOS" | "PENDIENTE" | "ACEPTADA" | "EN_PROCESO" | "RECHAZADA" | "COMPLETADA" | "CANCELADA";
type ModalidadFilter = "TODAS" | "DIRECTA" | "ABIERTA";

function getRequestModality(req: TechnicianDirectRequest): "DIRECTA" | "ABIERTA" {
  const mod = (req.modalidad || "").trim().toUpperCase();
  if (mod === "ABIERTA" || mod === "OPEN") return "ABIERTA";
  if (mod === "DIRECTA" || mod === "DIRECT") return "DIRECTA";
  return (req.servicio_nombre || "").includes("(Licitación abierta)") ? "ABIERTA" : "DIRECTA";
}

const estadoConfig: Record<
  string,
  { label: string; bg: string; color: string; border: string; icon: typeof Clock3 }
> = {
  PENDIENTE: { label: "Pendiente", bg: "#fff7ed", color: "#9a3412", border: "#fed7aa", icon: Clock3 },
  ACEPTADA: { label: "Aceptada", bg: "#f0fdf4", color: "#166534", border: "#bbf7d0", icon: CheckCircle2 },
  EN_PROCESO: { label: "En proceso", bg: "#eff6ff", color: "#1d4ed8", border: "#bfdbfe", icon: Clock3 },
  RECHAZADA: { label: "Rechazada", bg: "#f8fafc", color: "#475569", border: "#cbd5e1", icon: XCircle },
  CANCELADA: { label: "Cancelada", bg: "#f8fafc", color: "#475569", border: "#cbd5e1", icon: XCircle },
  COMPLETADA: { label: "Completada", bg: "#f0fdf4", color: "#166534", border: "#bbf7d0", icon: CheckCircle2 },
};

function normalizeEstado(estado?: string): string {
  const raw = (estado || "PENDIENTE").trim().toUpperCase();
  if (raw === "EN PROCESO") return "EN_PROCESO";
  return raw;
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "Fecha por confirmar";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("es-HN", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function TechnicianRequestsManager({ user, initialRequestId = "" }: { user: User; initialRequestId?: string }) {
  const [requests, setRequests] = useState<DirectRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState("");
  const [completionNotice, setCompletionNotice] = useState<{ id: string; message: string } | null>(null);
  const [startedRequestId, setStartedRequestId] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const [filter, setFilter] = useState<EstadoFilter>("TODOS");
  const [ratingTarget, setRatingTarget] = useState<{
    tipo_solicitud: "DIRECTA" | "ABIERTA";
    solicitud_id: string;
    cliente_nombre: string;
  } | null>(null);
  const [reviewedState, setReviewedState] = useState<Record<string, { sent: boolean; closed: boolean }>>({});
  const [reportIncidentSolicitudId, setReportIncidentSolicitudId] = useState<string | null>(null);
  const [cancelTarget, setCancelTarget] = useState<{ id: string; estado: string } | null>(null);
  const [startConfirmationId, setStartConfirmationId] = useState<string | null>(null);
  const [trackingId, setTrackingId] = useState("");
  const [trackingModality, setTrackingModality] = useState<"DIRECTA" | "ABIERTA">("DIRECTA");
  const [tracking, setTracking] = useState<TechnicianDirectRequestDetail | TechnicianOpportunityItem | null>(null);
  const [trackingError, setTrackingError] = useState("");
  const [openChatId, setOpenChatId] = useState<string | null>(null);

  useEffect(() => {
    if (!openChatId) return;
    const target = requests.find((item) => item.id === openChatId);
    if (!target || !isChatEnabledForState(target.estado)) {
      setOpenChatId(null);
    }
  }, [openChatId, requests]);

  useEffect(() => {
    if (!trackingId) return;
    let active = true;
    const loadDetail = trackingModality === "ABIERTA" ? getOpportunityDetailApi : getTechnicianDirectRequest;
    loadDetail(user, trackingId)
      .then((detail) => {
        if (active) setTracking(detail);
      })
      .catch((err) => {
        if (active) setTrackingError(errorMessage(err));
      });
    return () => { active = false; };
  }, [user, trackingId, trackingModality, refreshTick]);

  // HU-22: Consultar Solicitudes directas recibidas desde el backend
  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError("");

    listTechnicianDirectRequests(user)
      .then((data) => {
        if (!active) return;
        const rows = Array.isArray(data) ? data : [];
        setRequests(rows);
        if (initialRequestId) {
          const matched = rows.find((item) => item.id === initialRequestId);
          if (matched) {
            setTrackingId(matched.id);
            setTrackingModality(getRequestModality(matched));
          }
        }
      })
      .catch((err) => {
        if (!active) return;
        setLoadError(errorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [user, refreshTick, initialRequestId]);

  const [modalityFilter, setModalityFilter] = useState<ModalidadFilter>("TODAS");

  // HU-47 (CA-05): Actualización automática tras avisos en vivo de propuestas o estados
  useEffect(() => {
    const unsubProposal = subscribeProposalLiveUpdate(() => {
      setRefreshTick((v) => v + 1);
    });
    const unsubGeneral = subscribeGeneralNotificationLiveUpdate(() => {
      setRefreshTick((v) => v + 1);
    });
    return () => {
      unsubProposal();
      unsubGeneral();
    };
  }, []);

  async function handleDecision(id: string, decision: "accept" | "reject") {
    setActionLoading(id);
    setActionError("");
    setActionNotice("");
    const successText =
      decision === "accept"
        ? "Solicitud aceptada correctamente."
        : "Solicitud rechazada correctamente.";

    try {
      const updated = decision === "accept"
        ? await acceptTechnicianDirectRequest(user, id)
        : await rejectTechnicianDirectRequest(user, id);
      setRequests((prev) =>
        prev.map((req) => (req.id === id ? { ...req, estado: updated.estado } : req)),
      );
      if (trackingId === id) setRefreshTick((tick) => tick + 1);
      setActionNotice(successText);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setActionLoading(null);
    }
  }

  // HU-23: Aceptar Solicitud
  function handleAccept(id: string) {
    return handleDecision(id, "accept");
  }

  // HU-24: Rechazar Solicitud
  function handleReject(id: string) {
    return handleDecision(id, "reject");
  }

  // Iniciar ejecución del servicio (pasa de ACEPTADA a EN_PROCESO)
  async function handleStartExecution(id: string) {
    if (actionLoading) return;
    setStartConfirmationId(null);
    setActionLoading(id);
    setActionError("");
    setActionNotice("");
    setStartedRequestId(null);

    try {
      const updated = await startTechnicianDirectRequest(user, id);
      setRequests((prev) =>
        prev.map((req) =>
          req.id === id ? { ...req, ...updated, estado: "EN_PROCESO" } : req,
        ),
      );
      if (trackingId === id) setRefreshTick((tick) => tick + 1);
      setStartedRequestId(id);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setActionLoading(null);
    }
  }

  // HU-34: Reportar trabajo finalizado (conserva En proceso con pendiente_recepcion e inicia plazo de 48h)
  async function handleReportCompletion(id: string) {
    setActionLoading(id);
    setActionError("");
    setActionNotice("");
    setCompletionNotice(null);

    try {
      const res = await reportTechnicianRequestCompletion(user, id);
      setRequests((prev) =>
        prev.map((req) =>
          req.id === id
            ? {
                ...req,
                estado: "EN_PROCESO",
                pendiente_recepcion: true,
                finalizacion_reportada_en: res.finalizacion_reportada_en,
                vencimiento_confirmacion_en: res.vencimiento_confirmacion_en,
                finalizacion_reportada_local: res.finalizacion_reportada_local,
                vencimiento_confirmacion_local: res.vencimiento_confirmacion_local,
              }
            : req,
        ),
      );
      setCompletionNotice({
        id,
        message: res.mensaje ||
          "Finalización reportada exitosamente. El servicio permanece En proceso pendiente de recepción y se inició el plazo de 48 horas.",
      });
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setActionLoading(null);
    }
  }

  if (loading) {
    return (
      <LoadingState
        message="Cargando solicitudes entrantes."
        detail="Consultando las peticiones directas que has recibido."
      />
    );
  }

  const countByState = (estado: string) =>
    requests.filter((r) => normalizeEstado(r.estado) === estado).length;
  const directCount = requests.filter((r) => getRequestModality(r) === "DIRECTA").length;
  const openCount = requests.filter((r) => getRequestModality(r) === "ABIERTA").length;
  const pendingCount = countByState("PENDIENTE");
  const filtered = requests.filter((r) => {
    const matchesState = filter === "TODOS" || normalizeEstado(r.estado) === filter;
    const matchesModality = modalityFilter === "TODAS" || getRequestModality(r) === modalityFilter;
    return matchesState && matchesModality;
  });

  return (
    <div>
      {/* Encabezado */}
      <div className="client-requests-page-heading" style={{ marginBottom: "24px" }}>
        <div>
          <h1 style={{ fontSize: "clamp(28px, 4vw, 38px)", letterSpacing: "-1.3px", marginBottom: "8px" }}>
            Mis solicitudes y trabajos
          </h1>
          <p style={{ color: "var(--muted)", fontSize: "14px", margin: 0 }}>
            Gestiona tus solicitudes directas y los trabajos de ofertas abiertas donde fuiste seleccionado.
          </p>
        </div>
        {pendingCount > 0 && (
          <span
            className="status-pill"
            style={{ background: "#fff7ed", color: "#9a3412", fontWeight: 700, alignSelf: "flex-start" }}
          >
            <Clock3 size={15} />
            {pendingCount} pendiente{pendingCount !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {/* Filtros y actualización */}
      <div className="client-requests-toolbar">
        <div className="client-requests-filter-controls">
          <div className="client-requests-state-filter">
            <label htmlFor="filter-modalidad">Modalidad</label>
            <select
              id="filter-modalidad"
              value={modalityFilter}
              onChange={(e) => setModalityFilter(e.target.value as ModalidadFilter)}
            >
              <option value="TODAS">Todas ({requests.length})</option>
              <option value="DIRECTA">Directas ({directCount})</option>
              <option value="ABIERTA">Abiertas ({openCount})</option>
            </select>
          </div>
          <div className="client-requests-state-filter">
            <label htmlFor="filter-estado">Filtrar por estado</label>
            <select
              id="filter-estado"
              value={filter}
              onChange={(e) => setFilter(e.target.value as EstadoFilter)}
            >
              <option value="TODOS">Todas ({requests.length})</option>
              <option value="PENDIENTE">Pendientes ({pendingCount})</option>
              <option value="ACEPTADA">Aceptadas ({countByState("ACEPTADA")})</option>
              <option value="EN_PROCESO">En proceso ({countByState("EN_PROCESO")})</option>
              <option value="CANCELADA">Canceladas ({countByState("CANCELADA")})</option>
              <option value="RECHAZADA">Rechazadas ({countByState("RECHAZADA")})</option>
              <option value="COMPLETADA">Completadas ({countByState("COMPLETADA")})</option>
            </select>
          </div>
        </div>
        <Button
          type="button"
          className="button-outline client-requests-refresh"
          onClick={() => setRefreshTick((tick) => tick + 1)}
          disabled={loading || actionLoading !== null}
        >
          <RefreshCw size={15} />
          Actualizar
        </Button>
      </div>

      {/* Notificaciones de éxito / error */}
      {actionNotice && (
        <div className="success-alert" role="status" style={{ margin: "16px 0" }}>
          <CheckCircle2 size={18} />
          <span>{actionNotice}</span>
        </div>
      )}
      {actionError && (
        <div className="alert" role="alert" style={{ marginTop: "16px" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
            <CircleAlert size={16} />
            {actionError}
          </span>
        </div>
      )}

      {/* Estado de error al cargar o lista de solicitudes */}
      {loadError ? (
        <div className="alert" role="alert" style={{ marginTop: "20px" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "12px",
              flexWrap: "wrap",
            }}
          >
            <span style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
              <CircleAlert size={16} />
              No pudimos cargar tus solicitudes entrantes: {loadError}
            </span>
            <Button
              type="button"
              className="button-outline"
              style={{ width: "auto", minHeight: "36px", padding: "6px 14px" }}
              onClick={() => setRefreshTick((tick) => tick + 1)}
            >
              Reintentar
            </Button>
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="catalog-empty" style={{ marginTop: "20px" }}>
          <Inbox size={40} />
          <h3>
            {filter === "TODOS"
              ? "No tienes solicitudes"
              : "No hay solicitudes en este estado"}
          </h3>
          <p>
            {filter === "TODOS"
              ? "Cuando un cliente te envíe una solicitud directa, aparecerá aquí para que la revises."
              : "No se encontraron solicitudes con el estado seleccionado."}
          </p>
        </div>
      ) : (
        <div className="client-requests-list" style={{ marginTop: "20px" }}>
          {filtered.map((req) => {
            const normalizedState = normalizeEstado(req.estado);
            const config = estadoConfig[normalizedState] ?? estadoConfig.PENDIENTE;
            const StatusIcon = config.icon;
            const modality = getRequestModality(req);
            const clientName = req.cliente_nombre || "Cliente";
            const totalEstimado = Number((req as any).monto_total ?? req.total_estimado ?? 0);
            const isPendingReception =
              normalizedState === "EN_PROCESO" &&
              !req.incidencia_abierta &&
              Boolean(req.pendiente_recepcion || req.finalizacion_reportada_en || req.finalizacion_reportada_local);
            const hasPendingAdjustment = Boolean(req.tiene_ajuste_pendiente);
            const reportedAtIso = req.finalizacion_reportada_local || req.finalizacion_reportada_en;
            const deadlineIso = req.vencimiento_confirmacion_local || req.vencimiento_confirmacion_en;
            const deadlineStatus = isPendingReception ? formatConfirmationWindowStatus(deadlineIso) : null;
            const requestNotice = normalizedState === "EN_PROCESO" && !req.incidencia_abierta
              ? isPendingReception
                ? completionNotice?.id === req.id ? completionNotice.message : null
                : startedRequestId === req.id ? "Servicio iniciado. Ahora se encuentra En proceso y puedes reportar su finalización al terminar." : null
              : null;

            return (
              <div key={req.id} className="client-request-item" style={{ flexWrap: "wrap" }}>
                <div className="client-request-item-main">
                  {/* Nombre del cliente y estado */}
                  <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px", flex: 1, minWidth: 0 }}>
                      <span
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          width: "38px",
                          height: "38px",
                          borderRadius: "50%",
                          background: "#eff6ff",
                          color: "var(--blue)",
                          fontSize: "14px",
                          fontWeight: 750,
                          flexShrink: 0,
                        }}
                      >
                        {clientName.charAt(0).toUpperCase()}
                      </span>
                      <h3 style={{ margin: 0, fontSize: "16px" }}>{clientName}</h3>
                    </div>
                    <span
                      className="status-pill"
                      style={{
                        background: modality === "ABIERTA" ? "#eef2ff" : "#f8fafc",
                        color: modality === "ABIERTA" ? "#3730a3" : "#475569",
                        border: `1px solid ${modality === "ABIERTA" ? "#c7d2fe" : "#cbd5e1"}`,
                        fontWeight: 650,
                        fontSize: "11px",
                      }}
                    >
                      {modality === "ABIERTA" ? "Solicitud abierta" : "Solicitud directa"}
                    </span>
                                        <span
                      className="status-pill"
                      style={{
                        background: req.incidencia_abierta ? "#fef2f2" : config.bg,
                        color: req.incidencia_abierta ? "#b91c1c" : config.color,
                        border: `1px solid ${req.incidencia_abierta ? "#fecaca" : config.border}`,
                        fontWeight: 650,
                        fontSize: "11px",
                      }}
                    >
                      <StatusIcon size={14} />
                      {req.incidencia_abierta
                        ? "En revisión por incidencia"
                        : isPendingReception
                        ? "En proceso · Pendiente de recepción"
                        : config.label}
                    </span>
                  </div>

                  {/* Servicio */}
                  <p style={{ margin: "8px 0 0 48px", fontSize: "14px", fontWeight: 600, color: "var(--ink)" }}>
                    {req.servicio_nombre}
                  </p>

                  {/* Fecha */}
                  <div className="client-request-meta" style={{ marginLeft: "48px", marginTop: "6px" }}>
                    <span>
                      <Calendar size={14} />
                      {formatDateTime(req.fecha_hora)}
                    </span>
                  </div>

                  {/* Descripción */}
                  <div
                    style={{
                      margin: "12px 0 0 48px",
                      padding: "12px 16px",
                      background: "var(--surface)",
                      border: "1px solid var(--line)",
                      borderRadius: "10px",
                      fontSize: "13px",
                      color: "var(--muted)",
                      lineHeight: 1.7,
                    }}
                  >
                    "{req.descripcion}"
                  </div>

                  {/* HU-31 CA-06 / HU-23 / HU-35: Tarjeta de Ubicación y Contacto Reutilizable */}
                  <ServiceLocationContactCard
                    municipio={req.municipio}
                    sector={req.sector}
                    direccionExacta={req.direccion_exacta}
                    telefono={req.cliente_telefono}
                    isPendingAcceptance={normalizedState === "PENDIENTE"}
                  />

                  {/* Total */}
                  <div style={{ margin: "12px 0 0 48px", display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                    <span style={{ fontSize: "13px", color: "var(--muted)" }}>Total a cobrar:</span>
                    <strong style={{ fontSize: "17px", color: "var(--blue)", fontWeight: 800 }}>
                      L. {Number.isFinite(totalEstimado) ? totalEstimado.toFixed(2) : "0.00"}
                    </strong>
                    {Number((req as any).monto_ajuste || 0) !== 0 && (req as any).desglose_medios?.monto_base && (
                      <span style={{ fontSize: "12px", color: "var(--muted)" }}>
                        (base original: L. {(req as any).desglose_medios.monto_base})
                      </span>
                    )}
                  </div>

                  {/* HU-34 CA-02 & CA-03: Visualización de pendiente de recepción y vencimiento de 48h en America/Tegucigalpa */}
                                    {/* HU-35 CA-04: Aviso al técnico cuando el cliente abre una incidencia */}
                                    {req.incidencia_abierta && (
                    <div
                      role="alert"
                      style={{
                        margin: "12px 0 0 48px",
                        padding: "12px 14px",
                        background: "#fef2f2",
                        border: "1px solid #fecaca",
                        borderRadius: "10px",
                        fontSize: "13px",
                        color: "#991b1b",
                        lineHeight: 1.6,
                      }}
                    >
                      {(req as any).es_disputa_cruzada ? (
                        <>
                          <div style={{ fontWeight: 700, marginBottom: "4px" }}>
                            Disputa bilateral en mediación administrativa · Cierre automático y fondos retenidos
                          </div>
                          <div>
                            Ambas partes (tú y el cliente) han registrado sus reportes y evidencias sobre este servicio. La administración de TekniGO está evaluando el caso para emitir una resolución.
                          </div>
                        </>
                      ) : (req as any).tecnico_reporto_incidencia || (req.incidencia as any)?.reportante_rol === "TECNICO" ? (
                        <>
                          <div style={{ fontWeight: 700, marginBottom: "4px" }}>
                            Incidencia o justificación reportada por ti · Cierre automático y fondos retenidos
                          </div>
                          <div>
                            Has registrado un reporte o justificación técnica para este servicio. El cierre automático se ha detenido y el expediente está en revisión administrativa.
                          </div>
                        </>
                      ) : (
                        <>
                          <div style={{ fontWeight: 700, marginBottom: "4px" }}>
                            Incidencia reportada por el cliente · Cierre automático y fondos retenidos
                          </div>
                          <div>
                            El cliente abrió una incidencia dentro del plazo de 48 horas. El cierre automático se ha detenido y la liberación de fondos permanece retenida hasta la resolución administrativa.
                          </div>
                        </>
                      )}
                      {req.incidencia?.motivo && (
                        <div style={{ marginTop: "6px" }}>
                          <strong>Motivo reportado:</strong> {req.incidencia.motivo}
                        </div>
                      )}
                    </div>
                  )}
                  {isPendingReception && (
                    <div
                      role="region"
                      aria-label="Plazo de confirmación del servicio"
                      style={{
                        margin: "12px 0 0 48px",
                        padding: "12px 14px",
                        background: "#eff6ff",
                        border: "1px solid #bfdbfe",
                        borderRadius: "10px",
                        fontSize: "13px",
                        color: "#1e3a8a",
                        lineHeight: 1.6,
                      }}
                    >
                      <div style={{ fontWeight: 700, marginBottom: "4px" }}>
                        Trabajo reportado como finalizado · Pendiente de recepción del cliente
                      </div>
                      <div>
                        El servicio permanece En proceso y los fondos continúan retenidos hasta la confirmación o el cierre del plazo.
                      </div>
                      {reportedAtIso && (
                        <div>
                          <strong>Finalización reportada:</strong> {formatTegucigalpaDateTime(reportedAtIso)}
                        </div>
                      )}
                      {deadlineIso && (
                        <div>
                          <strong>Vencimiento de confirmación (48 h):</strong> {formatTegucigalpaDateTime(deadlineIso)}
                        </div>
                      )}
                      {deadlineStatus && (
                        <div style={{ marginTop: "4px", fontWeight: 600 }}>{deadlineStatus.etiqueta}</div>
                      )}
                    </div>
                  )}

                  {/* HU-34 CA-05: Aviso si existe un ajuste pendiente de aprobación */}
                  {normalizedState === "EN_PROCESO" && !isPendingReception && hasPendingAdjustment && (
                    <div
                      role="note"
                      style={{
                        margin: "12px 0 0 48px",
                        padding: "10px 14px",
                        background: "#fffbeb",
                        border: "1px solid #fde68a",
                        borderRadius: "10px",
                        fontSize: "13px",
                        color: "#92400e",
                      }}
                    >
                      <strong>Ajuste pendiente de aprobación:</strong>{" "}
                      {req.motivo_ajuste_pendiente ||
                        "No se permite reportar el servicio como terminado hasta resolver el ajuste pendiente."}
                    </div>
                  )}
                </div>

                <div className="client-request-item-side">
                  <Button
                    type="button"
                    className="button-outline"
                    aria-expanded={trackingId === req.id}
                    onClick={() => {
                      setTracking(null);
                      setTrackingError("");
                      setTrackingModality(modality);
                      setTrackingId(trackingId === req.id ? "" : req.id);
                    }}
                  >
                    {trackingId === req.id ? "Ocultar seguimiento" : "Ver seguimiento"}
                  </Button>
                </div>

                {/* Botones de acción */}
                {normalizedState === "PENDIENTE" && (
                  <div className="client-request-item-side" style={{ flexDirection: "column", gap: "8px" }}>
                    <Button
                      type="button"
                      onClick={() => handleAccept(req.id)}
                      disabled={actionLoading === req.id}
                      style={{
                        background: "#f0fdf4",
                        color: "#166534",
                        border: "1px solid #bbf7d0",
                        gap: "8px",
                        minWidth: "130px",
                        fontSize: "13px",
                        fontWeight: 700,
                      }}
                    >
                      <Check size={16} strokeWidth={2.5} />
                      {actionLoading === req.id ? "Procesando…" : "Aceptar"}
                    </Button>
                    <Button
                      type="button"
                      onClick={() => handleReject(req.id)}
                      disabled={actionLoading === req.id}
                      style={{
                        background: "#fef2f2",
                        color: "#991b1b",
                        border: "1px solid #fecaca",
                        gap: "8px",
                        minWidth: "130px",
                        fontSize: "13px",
                        fontWeight: 700,
                      }}
                    >
                      <X size={16} strokeWidth={2.5} />
                      {actionLoading === req.id ? "Procesando…" : "Rechazar"}
                    </Button>
                  </div>
                )}
                {requestNotice && (
                  <div className="success-alert" role="status" style={{ flexBasis: "100%", margin: 0 }}>
                    <CheckCircle2 size={18} aria-hidden="true" />
                    <span>{requestNotice}</span>
                  </div>
                )}
                {trackingId === req.id && (
                  <div style={{ flexBasis: "100%" }}>
                    <p>{modality === "ABIERTA" ? "Solicitud abierta" : "Solicitud directa"} · {estadoConfig[tracking?.estado ?? normalizedState]?.label ?? normalizedState}</p>
                    {trackingError && <p role="alert">{trackingError}</p>}
                    {!tracking && !trackingError && <p role="status">Cargando seguimiento…</p>}
                    {tracking?.linea_tiempo && <RequestTimeline items={tracking.linea_tiempo} />}
                  </div>
                )}

                {isChatEnabledForState(req.estado) && (
                  <div style={{ flexBasis: "100%", marginTop: "8px" }}>
                    <Button
                      type="button"
                      className="button-outline"
                      style={{ width: "auto", gap: "6px", fontSize: "13px" }}
                      onClick={() => setOpenChatId((prev) => (prev === req.id ? null : req.id))}
                    >
                      <MessageSquare size={15} />
                      {openChatId === req.id ? "Ocultar chat" : "Abrir chat con cliente"}
                    </Button>
                    {openChatId === req.id && (
                      <ContractChatPanel
                        user={user}
                        modalidad={modality}
                        solicitudId={req.id}
                        estadoSolicitud={req.estado}
                        currentRole="TECNICO"
                        counterpartName={req.cliente_nombre || "Cliente"}
                        onChatClosed={() => setRefreshTick((v) => v + 1)}
                      />
                    )}
                  </div>
                )}

                  {/* Botón para pasar de ACEPTADA a EN_PROCESO */}
                  {normalizedState === "ACEPTADA" && (
                  <div className="client-request-item-side" style={{ flexDirection: "column", gap: "8px" }}>
                    {!req.incidencia_abierta && (
                      <Button
                        type="button"
                        onClick={() => setStartConfirmationId(req.id)}
                        disabled={actionLoading !== null}
                        style={{
                          background: "#eff6ff",
                          color: "#1d4ed8",
                          border: "1px solid #bfdbfe",
                          gap: "8px",
                          minWidth: "170px",
                          fontSize: "13px",
                          fontWeight: 700,
                        }}
                      >
                        <Clock3 size={16} />
                        {actionLoading === req.id ? "Iniciando…" : "Iniciar servicio"}
                      </Button>
                    )}
                      {(req as any).tecnico_reporto_incidencia ? (
                      <Button type="button" className="button-outline" disabled style={{ fontSize: "12px", minWidth: "170px" }}>
                        <CheckCircle2 size={14} color="#16a34a" /> Reporte enviado (En revisión)
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        className="button-outline"
                        style={{ color: "#b91c1c", borderColor: "#fca5a5", fontSize: "12px", minWidth: "170px" }}
                        onClick={() => setReportIncidentSolicitudId(req.id)}
                      >
                        <AlertTriangle size={14} /> Reportar incidencia o justificación
                      </Button>
                    )}
                    <Button
                      type="button"
                      className="button-outline"
                      style={{ fontSize: "12px", minWidth: "170px" }}
                      onClick={() => setCancelTarget({ id: req.id, estado: normalizedState })}
                    >
                      Cancelar servicio
                    </Button>
                  </div>
                )}

                {(() => {
                  if (normalizedState !== "COMPLETADA") {
                    return null;
                  }
                  const normReqId = (req.id || "").trim().toLowerCase();
                  const localReview = reviewedState[req.id] ?? reviewedState[normReqId];
                  const evaluacionCerrada = Boolean(req.evaluacion_cerrada || localReview?.closed);
                  if (evaluacionCerrada) {
                    return null;
                  }
                  const miResenaEnviada = Boolean(req.mi_resena_enviada || localReview?.sent);
                  return (
                    <div className="client-request-item-side" style={{ flexDirection: "column", gap: "8px" }}>
                      {miResenaEnviada ? (
                        <Button
                          type="button"
                          disabled
                          style={{
                            background: "#f3f4f6",
                            color: "#6b7280",
                            border: "1px solid #d1d5db",
                            cursor: "not-allowed",
                            opacity: 0.85,
                            gap: "6px",
                            minWidth: "140px",
                            fontSize: "13px",
                            fontWeight: 700,
                          }}
                        >
                          <Star size={16} fill="none" />
                          Evaluación enviada
                        </Button>
                      ) : (
                        <Button
                          type="button"
                          onClick={() =>
                            setRatingTarget({
                              tipo_solicitud: modality,
                              solicitud_id: req.id,
                              cliente_nombre: req.cliente_nombre || "Cliente",
                            })
                          }
                          style={{
                            background: "#fefce8",
                            color: "#854d0e",
                            border: "1px solid #fef08a",
                            gap: "6px",
                            minWidth: "140px",
                            fontSize: "13px",
                            fontWeight: 700,
                          }}
                        >
                          <Star size={16} fill="currentColor" />
                          Calificar cliente
                        </Button>
                      )}
                    </div>
                  );
                })()}

                {/* HU-34 CA-01 a CA-05: Botón para reportar trabajo finalizado o incidencia desde En proceso */}
                {normalizedState === "EN_PROCESO" && !isPendingReception && !req.incidencia_abierta && (
                  <div className="client-request-item-side" style={{ flexDirection: "column", gap: "8px" }}>
                    {!isPendingReception && (
                      <Button
                        type="button"
                        onClick={() => handleReportCompletion(req.id)}
                        disabled={actionLoading === req.id || hasPendingAdjustment}
                        style={{
                          background: hasPendingAdjustment ? "#f1f5f9" : "#eff6ff",
                          color: hasPendingAdjustment ? "#64748b" : "#1d4ed8",
                          border: `1px solid ${hasPendingAdjustment ? "#cbd5e1" : "#bfdbfe"}`,
                          gap: "8px",
                          minWidth: "170px",
                          fontSize: "13px",
                          fontWeight: 700,
                        }}
                      >
                        <CheckCircle2 size={16} />
                        {actionLoading === req.id ? "Reportando…" : "Reportar trabajo finalizado"}
                      </Button>
                    )}
                    {(req.incidencia as any)?.reportante_rol === "TECNICO" ? (
                      <Button type="button" className="button-outline" disabled style={{ fontSize: "12px", minWidth: "170px" }}>
                        <CheckCircle2 size={14} color="#16a34a" /> Justificación enviada (En revisión)
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        className="button-outline"
                        style={{ color: "#b91c1c", borderColor: "#fca5a5", fontSize: "12px", minWidth: "170px" }}
                        onClick={() => setReportIncidentSolicitudId(req.id)}
                      >
                        <AlertTriangle size={14} /> Reportar incidencia / justificación
                      </Button>
                    )}
                    <Button
                      type="button"
                      className="button-outline"
                      style={{ fontSize: "12px", minWidth: "170px" }}
                      onClick={() => setCancelTarget({ id: req.id, estado: normalizedState })}
                    >
                      Cancelar servicio
                    </Button>
                  </div>
                )}
                {normalizedState === "EN_PROCESO" && !req.incidencia_abierta && (
                  <div style={{ flexBasis: "100%", marginTop: "12px" }}>
                    <AdjustmentPanel
                      user={user} solicitudId={req.id} rol="TECNICO" estado={req.estado}
                      montoBase={String((req as any).desglose_medios?.monto_total ?? (req as any).monto_total ?? req.total_estimado)}
                      onChanged={() => setRefreshTick((v) => v + 1)}
                    />
                  </div>
                )}
                {normalizedState === "CANCELADA" && (
                  <div style={{ flexBasis: "100%", marginTop: "12px" }}>
                    <CancellationNotice user={user} solicitudId={req.id} rol="TECNICO"
                      onChanged={() => setRefreshTick((v) => v + 1)} />
                  </div>
                )}
              </div>
            );
          })}
          
        </div>
      )}

      {ratingTarget && (
        <ReviewModal
          user={user}
          tipo_solicitud={ratingTarget.tipo_solicitud}
          solicitud_id={ratingTarget.solicitud_id}
          evaluadoNombre={ratingTarget.cliente_nombre}
          rolEvaluador="TECNICO"
          onClose={() => setRatingTarget(null)}
          onSuccess={(res) => {
            const targetId = ratingTarget.solicitud_id;
            const normTargetId = targetId.trim().toLowerCase();
            const closedNow = Boolean(
              res.publicada ?? res.publicada_inmediata ?? res.estado_publicacion === "PUBLICADA",
            );
            setReviewedState((prev) => ({
              ...prev,
              [targetId]: { sent: true, closed: closedNow },
              [normTargetId]: { sent: true, closed: closedNow },
            }));
            setRequests((prev) =>
              prev.map((item) =>
                item.id.trim().toLowerCase() === normTargetId
                  ? { ...item, mi_resena_enviada: true, evaluacion_cerrada: closedNow }
                  : item,
              ),
            );
            setActionNotice(res.mensaje);
            setRefreshTick((v) => v + 1);
          }}
        />
      )}

      {/* Paso 3.4: Modal de reporte e incidencias para el técnico montado a nivel de página (CA-01, CA-05) */}
      {reportIncidentSolicitudId && (
        <ReportIncidentModal
          isOpen={Boolean(reportIncidentSolicitudId)}
          onClose={() => setReportIncidentSolicitudId(null)}
          onSuccess={(msg) => {
            setActionNotice(msg);
            setRefreshTick((v) => v + 1);
          }}
          user={user}
          solicitudId={reportIncidentSolicitudId}
          rol="TECNICO"
        />
        
      )}
      {cancelTarget && (
        <CancelRequestModal
          isOpen onClose={() => setCancelTarget(null)}
          user={user} solicitudId={cancelTarget.id} rol="TECNICO" estado={cancelTarget.estado}
          onDone={(msg) => { setActionNotice(msg); setRefreshTick((v) => v + 1); }}
        />
      )}
      {startConfirmationId && (
        <StartServiceConfirmation
          onCancel={() => setStartConfirmationId(null)}
          onConfirm={() => void handleStartExecution(startConfirmationId)}
        />
      )}
    </div>
  );
}

