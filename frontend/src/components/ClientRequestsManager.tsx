import RequestTimeline from "./RequestTimeline";
import { useEffect, useId, useRef, useState } from "react";
import type { User } from "firebase/auth";
import { Link } from "react-router-dom";
import {
  ArrowUpRight,
  AlertTriangle,
  Award,
  Calendar,
  CheckCircle2,
  ClipboardList,
  Clock3,
  FileText,
  MapPin,
  RefreshCw,
  ShieldCheck,
  Star,
  UserCheck,
  WifiOff,
  X,
} from "lucide-react";
import ReviewModal from "./ReviewModal";
import { Button } from "./ui";
import { errorMessage } from "../lib/api";
import CancellationNotice from "./CancellationNotice";
import AdjustmentPanel from "./AdjustmentPanel";
import { CancelRequestModal } from "./CancellationForms";
import ReportIncidentModal from "./ReportIncidentModal";
import ContractChatPanel from "./ContractChatPanel";
import { isChatEnabledForState } from "../lib/contractChat";
import SecurePaymentGatewayModal, { type CardPaymentResult } from "./SecurePaymentGatewayModal";
import {
  ESTADO_OPTIONS,
  MODALIDAD_OPTIONS,
  TEGUCIGALPA_TIME_ZONE,
  awardProposalApi,
  confirmReceptionApi,
  executeMyRequestAction,
  formatDateTimeTegucigalpa,
  formatConfirmationWindowStatus,
  formatLempiras,
  formatRequestModalityLabel,
  formatRequestStateLabel,
  formatTegucigalpaDateTime,
  formatUnitLabel,
  getMyRequestDetail,
  listMyRequests,
  openServiceIncidentApi,
  rejectProposalApi,
  resolveReceptionUiState,
  type ClientRequestDetail,
  type ClientRequestsPageResponse,
  type EstadoFiltro,
  type ModalidadFiltro,
  type ReceivedOpenRequestProposal
} from "../lib/clientRequests";

const PAGE_SIZE = 10;
const JOB_STATES = ["ACEPTADA", "EN_PROCESO", "COMPLETADA"];
const JOB_STATUS_OPTIONS = ESTADO_OPTIONS.filter(({ value }) =>
  value === "TODOS" || JOB_STATES.includes(value),
);
const REQUEST_STATUS_OPTIONS = ESTADO_OPTIONS.filter(({ value }) => !JOB_STATES.includes(value));

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "Sin fecha registrada";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("es-HN", {
    timeZone: TEGUCIGALPA_TIME_ZONE,
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function StateBadge({
  estado,
  pendienteRecepcion,
  incidenciaAbierta,
}: {
  estado: string;
  pendienteRecepcion?: boolean;
  incidenciaAbierta?: boolean;
}) {
  const normalized = (estado || "PENDIENTE").toUpperCase();
  const slug = normalized.toLowerCase().replaceAll(/\s+/g, "_");
  return (
    <span
      className={`request-status-badge request-status-${slug}`}
      style={
        incidenciaAbierta
          ? { background: "#fef2f2", color: "#b91c1c", borderColor: "#fecaca" }
          : undefined
      }
    >
      {formatRequestStateLabel(normalized, pendienteRecepcion, incidenciaAbierta)}
    </span>
  );
}

function ModalityBadge({ modalidad }: { modalidad: string }) {
  const normalized = (modalidad || "DIRECTA").toUpperCase();
  return (
    <span className={`request-modality-badge request-modality-${normalized.toLowerCase()}`}>
      {formatRequestModalityLabel(normalized)}
    </span>
  );
}

function RequestDetailModal({
  children,
  onClose,
}: {
  children: React.ReactNode;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    if (typeof element.showModal === "function") {
      element.showModal();
    }
    document.body.style.overflow = "hidden";
    return () => {
      if (typeof element.close === "function") {
        element.close();
      }
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialog}
      className="dossier-modal client-request-modal"
      aria-labelledby={titleId}
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < bounds.left ||
          event.clientX > bounds.right ||
          event.clientY < bounds.top ||
          event.clientY > bounds.bottom
        ) {
          onClose();
        }
      }}
    >
      <header className="dossier-modal-heading">
        <h2 id={titleId}>Detalle de contratación</h2>
        <button
          type="button"
          className="dossier-modal-close"
          aria-label="Cerrar detalle de solicitud"
          onClick={onClose}
          autoFocus
        >
          <X size={20} />
        </button>
      </header>
      <div className="dossier-modal-body">{children}</div>
    </dialog>
  );
}

const ACTION_LABELS: Record<string, { label: string; hint: string }> = {
  CANCELAR: {
    label: "Cancelar solicitud",
    hint: "Puedes cancelar esta solicitud mientras no haya finalizado.",
  },
};

export default function ClientRequestsManager({
  user,
  initialRequestId = "",
  jobsOnly = false,
  onSelectRequestId,
}: {
  user: User;
  initialRequestId?: string;
  jobsOnly?: boolean;
  onSelectRequestId?: (id: string) => void;
}) {
  const [modalidad, setModalidad] = useState<ModalidadFiltro>("TODOS");
  const [estado, setEstado] = useState<EstadoFiltro>("TODOS");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ClientRequestsPageResponse | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);

  const [selectedId, setSelectedId] = useState(initialRequestId);
  const [detail, setDetail] = useState<ClientRequestDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [executingAction, setExecutingAction] = useState("");
  const [showCancel, setShowCancel] = useState(false);
  const [actionNotice, setActionNotice] = useState("");
  const [ratingTarget, setRatingTarget] = useState<{
    tipo_solicitud: "DIRECTA" | "ABIERTA";
    solicitud_id: string;
    evaluadoNombre: string;
  } | null>(null);
  const [reviewedIds, setReviewedIds] = useState<Record<string, boolean>>({});
  const [isOffline, setIsOffline] = useState(!navigator.onLine);

  //Manejar Estado de Conexión
  useEffect(() => {
    function handleOnline() {
      setIsOffline(false);
      if (selectedId) {
        setRefreshTick((v) => v + 1);
      }
    }
    function handleOffline() {
      setIsOffline(true);
    }
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [selectedId]);

  useEffect(() => {
    setSelectedId(initialRequestId);
  }, [initialRequestId]);

  useEffect(() => {
    let active = true;
    setLoadingList(true);
    setListError("");
    const stateFilter = estado === "TODOS" ? (jobsOnly ? "TRABAJOS" : "SOLICITUDES") : estado;
    listMyRequests(user, { modalidad, estado: stateFilter, offset, limit: PAGE_SIZE })
      .then((result) => {
        if (!active) return;
        setPage(result);
        if (result.solicitudes.length === 0 && offset > 0 && result.total > 0) {
          setOffset(Math.max(0, offset - PAGE_SIZE));
        }
      })
      .catch((err) => {
        if (active) setListError(errorMessage(err));
      })
      .finally(() => {
        if (active) setLoadingList(false);
      });
    return () => {
      active = false;
    };
  }, [user, modalidad, estado, offset, refreshTick, jobsOnly]);

  useEffect(() => {
    let active = true;
    setDetail(null);
    setDetailError("");
    if (!selectedId) {
      setLoadingDetail(false);
      return;
    }
    setLoadingDetail(true);
    getMyRequestDetail(user, selectedId)
      .then((result) => {
        if (active) setDetail(result);
      })
      .catch((err) => {
        if (active) setDetailError(errorMessage(err));
      })
      .finally(() => {
        if (active) setLoadingDetail(false);
      });
    return () => {
      active = false;
    };
  }, [user, selectedId, refreshTick]);

    // HU-29 ST-04 (CA-05): Actualización en tiempo real de nuevas ofertas y contraofertas
    //Se Realizaron cambios para la siguiente HU
    // HU-30 ST-04 (CA-05): Actualización en tiempo real y recuperación automática tras reconexión
  useEffect(() => {
    if (!detail || detail.modalidad !== "ABIERTA") return;
    const requestId = detail.id;
    const estado = (detail.estado || "").toUpperCase();
    if (estado !== "PUBLICADA" && estado !== "CON OFERTAS" && estado !== "CON_OFERTAS") return;

    function fetchLatest() {
      getMyRequestDetail(user, requestId)
        .then((updated) => {
          setDetail((prev) => {
            if (!prev || prev.id !== updated.id) return prev;
            const changed =
              JSON.stringify(prev.propuestas_recibidas) !== JSON.stringify(updated.propuestas_recibidas) ||
              prev.estado !== updated.estado;
            return changed ? updated : prev;
          });
        })
        .catch(() => {});
    }

    // CA-05: Al reconectar o enfocar la pestaña, volver a consultar los datos persistidos inmediatamente
    function handleFocusOrOnline() {
      fetchLatest();
    }

    window.addEventListener("focus", handleFocusOrOnline);
    window.addEventListener("online", handleFocusOrOnline);

    const interval = setInterval(fetchLatest, 3500);

    return () => {
      clearInterval(interval);
      window.removeEventListener("focus", handleFocusOrOnline);
      window.removeEventListener("online", handleFocusOrOnline);
    };
  }, [user, detail?.id, detail?.modalidad, detail?.estado]);

  function openDetail(id: string) {
    setActionNotice("");
    setSelectedId(id);
    onSelectRequestId?.(id);
  }

  function closeDetail() {
    setActionNotice("");
    setSelectedId("");
    onSelectRequestId?.("");
  }

  async function handleAction(accion: string) {
    if (!detail || executingAction) return;
    if (accion === "CANCELAR") {
      setShowCancel(true);
      return;
    }
    setExecutingAction(accion);
    setDetailError("");
    setActionNotice("");
    try {
      const updated = await executeMyRequestAction(user, detail.id, accion);
      setDetail(updated);
      setActionNotice(
        accion === "CANCELAR"
          ? "La solicitud fue cancelada correctamente."
          : "El estado de la solicitud se actualizó correctamente.",
      );
      setRefreshTick((value) => value + 1);
    } catch (err) {
      setDetailError(errorMessage(err));
    } finally {
      setExecutingAction("");
    }
  }

        const [confirmRejectId, setConfirmRejectId] = useState<string | null>(null);
  const [rejectingProposalId, setRejectingProposalId] = useState<string>("");
  const [confirmAwardProp, setConfirmAwardProp] = useState<ReceivedOpenRequestProposal | null>(null);
  const [awardPaymentMethod, setAwardPaymentMethod] = useState<"efectivo" | "tarjeta">("efectivo");
  const [showAwardGateway, setShowAwardGateway] = useState(false);
  const [awardingProposalId, setAwardingProposalId] = useState<string>("");

  async function executeRejectProposal(proposalId: string) {
    if (!detail || rejectingProposalId || awardingProposalId) return;
    setRejectingProposalId(proposalId);
    setDetailError("");
    setActionNotice("");
    try {
      const res = await rejectProposalApi(user, detail.id, proposalId);
      setActionNotice(res.mensaje || "Propuesta rechazada correctamente.");
      setConfirmRejectId(null);
      setDetail((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          estado: res.estado_solicitud,
          propuestas_recibidas: (prev.propuestas_recibidas || []).map((p) =>
            p.id === proposalId ? res.propuesta : p
          ),
        };
      });
      setRefreshTick((v) => v + 1);
    } catch (err) {
      setDetailError(errorMessage(err));
    } finally {
      setRejectingProposalId("");
    }
  }

  function startAwardConfirmation(prop: ReceivedOpenRequestProposal) {
    setConfirmRejectId(null);
    setDetailError("");
    const initialMethod =
      (detail?.metodo_pago || "efectivo").toLowerCase() === "tarjeta" ? "tarjeta" : "efectivo";
    setAwardPaymentMethod(initialMethod);
    setConfirmAwardProp(prop);
  }

  async function executeAwardProposal(
    prop: ReceivedOpenRequestProposal,
    metodo: "efectivo" | "tarjeta",
    referenciaPasarela?: string | null,
  ) {
    if (!detail || awardingProposalId) return;
    setAwardingProposalId(prop.id);
    setDetailError("");
    setActionNotice("");
    try {
      const idempotencyKey = `award-${detail.id}-${prop.id}-${metodo}`;
      const res = await awardProposalApi(user, detail.id, prop.id, {
        metodo_pago: metodo,
        referencia_pasarela: referenciaPasarela || null,
        idempotency_key: idempotencyKey,
      });
      setActionNotice(
        res.mensaje ||
          `Técnico adjudicado por ${formatLempiras(res.monto_pactado)} (${res.metodo_pago}).`,
      );
      setConfirmAwardProp(null);
      setShowAwardGateway(false);
      const updatedDetail = await getMyRequestDetail(user, detail.id);
      setDetail(updatedDetail);
      setRefreshTick((v) => v + 1);
    } catch (err) {
      setDetailError(errorMessage(err));
      setShowAwardGateway(false);
    } finally {
      setAwardingProposalId("");
    }
  }

  function handleConfirmAwardClick() {
    if (!confirmAwardProp) return;
    if (awardPaymentMethod === "tarjeta") {
      setShowAwardGateway(true);
      return;
    }
    void executeAwardProposal(confirmAwardProp, "efectivo", null);
  }

  function handleAwardCardSuccess(result: CardPaymentResult) {
    if (!confirmAwardProp) return;
    setShowAwardGateway(false);
    void executeAwardProposal(confirmAwardProp, "tarjeta", result.referenciaPasarela);
  }

  const [receptionComment, setReceptionComment] = useState("");
  const [confirmingReception, setConfirmingReception] = useState(false);
  const [showIncidentForm, setShowIncidentForm] = useState(false);
  const [showIncidentModal, setShowIncidentModal] = useState(false);
  const [incidentReason, setIncidentReason] = useState("");
  const [openingIncident, setOpeningIncident] = useState(false);

  async function handleConfirmReception() {
    if (!detail || confirmingReception) return;
    setConfirmingReception(true);
    setDetailError("");
    setActionNotice("");
    try {
      const res = await confirmReceptionApi(user, detail.id, receptionComment);
      setActionNotice(res.mensaje || "Recepción confirmada exitosamente.");
      setReceptionComment("");
      setShowIncidentForm(false);
      const updatedDetail = await getMyRequestDetail(user, detail.id);
      setDetail(updatedDetail);
      setRefreshTick((v) => v + 1);
    } catch (err) {
      setDetailError(errorMessage(err));
    } finally {
      setConfirmingReception(false);
    }
  }

  async function handleOpenIncident() {
    if (!detail || openingIncident) return;
    if (incidentReason.trim().length < 5) {
      setDetailError("Describe el motivo de la incidencia con al menos 5 caracteres.");
      return;
    }
    setOpeningIncident(true);
    setDetailError("");
    setActionNotice("");
    try {
      const res = await openServiceIncidentApi(user, detail.id, incidentReason);
      setActionNotice(res.mensaje || "Incidencia reportada. Se detuvo el cierre automático.");
      setIncidentReason("");
      setShowIncidentForm(false);
      const updatedDetail = await getMyRequestDetail(user, detail.id);
      setDetail(updatedDetail);
      setRefreshTick((v) => v + 1);
    } catch (err) {
      setDetailError(errorMessage(err));
    } finally {
      setOpeningIncident(false);
    }
  }

  return (
    <div className="client-requests-section">
      <div className="client-requests-toolbar">
        <div className="dossier-filters" role="group" aria-label="Filtrar por modalidad">
          {MODALIDAD_OPTIONS.map((opt) => (
            <Button
              key={opt.value}
              type="button"
              className={modalidad === opt.value ? "" : "button-outline"}
              aria-pressed={modalidad === opt.value}
              onClick={() => {
                setModalidad(opt.value);
                setOffset(0);
              }}
            >
              {opt.label}
            </Button>
          ))}
        </div>

        <div className="client-requests-filter-controls">
          <label className="client-requests-state-filter">
            <span>Estado</span>
            <select
              aria-label="Filtrar por estado"
              value={estado}
              onChange={(event) => {
                setEstado(event.target.value as EstadoFiltro);
                setOffset(0);
              }}
            >
              {(jobsOnly ? JOB_STATUS_OPTIONS : REQUEST_STATUS_OPTIONS).map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>

          <Button
            type="button"
            className="button-outline client-requests-refresh"
            onClick={() => setRefreshTick((value) => value + 1)}
          >
            <RefreshCw size={15} />
            Actualizar listado
          </Button>
        </div>
      </div>

      {listError && (
        <div className="alert" role="alert">
          {listError}
        </div>
      )}

      {loadingList && !page && <p role="status">Cargando tus solicitudes…</p>}

      {page && page.solicitudes.length === 0 && !listError && (
        <section className="catalog-empty client-requests-empty" role="status">
          <ClipboardList size={36} aria-hidden="true" />
          <h3>{jobsOnly ? "No hay trabajos para mostrar" : "No hay solicitudes para mostrar"}</h3>
          <p>
            {modalidad === "TODOS" && estado === "TODOS"
              ? jobsOnly
                ? "Aquí aparecerán tus trabajos cuando se acepte una solicitud directa o adjudiques una abierta."
                : "Aquí aparecerán las solicitudes que aún no se han convertido en trabajos y las canceladas o rechazadas."
              : "No se encontraron solicitudes que coincidan con los filtros seleccionados."}
          </p>
          {modalidad === "TODOS" && estado === "TODOS" ? (
            <Link className="button" to={jobsOnly ? "/mis-solicitudes" : "/catalogo"}>
              {jobsOnly ? "Ver mis solicitudes" : "Buscar servicios"}
            </Link>
          ) : (
            <Button
              type="button"
              className="button-outline"
              onClick={() => {
                setModalidad("TODOS");
                setEstado("TODOS");
                setOffset(0);
              }}
            >
              Limpiar filtros
            </Button>
          )}
        </section>
      )}

      {page && page.solicitudes.length > 0 && (
        <section className="account-card client-requests-card" aria-label="Historial de contrataciones">
          <div className="client-requests-card-header">
            <h2>{jobsOnly ? "Trabajos" : "Solicitudes encontradas"} ({page.total})</h2>
            <span>
              Mostrando {offset + 1}–{Math.min(offset + page.solicitudes.length, page.total)} de {page.total}
            </span>
          </div>

          <div className="client-requests-list">
                        {page.solicitudes.map((item) => {
              const itemReceptionUi = resolveReceptionUiState(item);
              return (
                <article
                  key={item.id}
                  className={`client-request-item${selectedId === item.id ? " client-request-item-selected" : ""}`}
                >
                  <div className="client-request-item-main">
                    <div className="client-request-badges">
                      <ModalityBadge modalidad={item.modalidad} />
                      <StateBadge
                        estado={item.estado}
                        pendienteRecepcion={itemReceptionUi.isPendingReceptionBadge}
                        incidenciaAbierta={itemReceptionUi.isBlockedByIncident}
                      />
                    </div>
                    <h3>{item.servicio?.nombre || "Servicio solicitado"}</h3>
                    <p className="client-request-meta">
                      <span>
                        <Calendar size={14} aria-hidden="true" /> Programado: {formatDateTime(item.fecha_hora)}
                      </span>
                      {item.tecnico?.nombre_completo ? (
                        <span>
                          <UserCheck size={14} aria-hidden="true" /> Técnico: {item.tecnico.nombre_completo}
                        </span>
                      ) : (
                        <span>
                          <Clock3 size={14} aria-hidden="true" /> Técnico: Pendiente de selección
                        </span>
                      )}
                      {item.municipio && (
                        <span>
                          <MapPin size={14} aria-hidden="true" /> {item.municipio}
                          {item.sector ? `, ${item.sector}` : ""}
                        </span>
                      )}
                      {itemReceptionUi.showConfirmationCountdown &&
                        (item.vencimiento_confirmacion_local || item.vencimiento_confirmacion_en) && (
                          <span>
                            <Clock3 size={14} aria-hidden="true" /> Vence confirmación (48 h):{" "}
                            {formatTegucigalpaDateTime(
                              item.vencimiento_confirmacion_local || item.vencimiento_confirmacion_en,
                            )}
                          </span>
                        )}
                    </p>
                  </div>

                  <div className="client-request-item-side">
                    <div className="client-request-budget">
                      <span>Presupuesto</span>
                      <strong>{formatLempiras((item as any).monto_total ?? item.presupuesto)}</strong>
                    </div>
                    <Button
                      type="button"
                      className="button-outline"
                      aria-label={`Ver detalle de ${item.servicio?.nombre || "solicitud"}`}
                      onClick={() => openDetail(item.id)}
                    >
                      <FileText size={15} />
                      Ver detalle
                    </Button>
                    {item.estado.toUpperCase() === "COMPLETADA" && item.tecnico?.nombre_completo && (
                      <Button
                        type="button"
                        className="button"
                        style={{
                          marginTop: "6px",
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 6,
                          fontSize: "0.85rem",
                          padding: "0.45rem 0.8rem",
                        }}
                        onClick={() =>
                          setRatingTarget({
                            tipo_solicitud: item.modalidad === "ABIERTA" ? "ABIERTA" : "DIRECTA",
                            solicitud_id: item.id,
                            evaluadoNombre: item.tecnico?.nombre_completo || "Técnico",
                          })
                        }
                      >
                        <Star size={14} fill="currentColor" />
                        {reviewedIds[item.id] ? "Calificación enviada" : "Calificar técnico"}
                      </Button>
                    )}
                  </div>
                </article>
              );
            })}
          </div>

          <div className="admin-toolbar admin-pagination">
            <Button
              type="button"
              className="button-outline"
              disabled={offset === 0}
              onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
            >
              Anterior
            </Button>
            <Button
              type="button"
              className="button-outline"
              disabled={offset + PAGE_SIZE >= page.total}
              onClick={() => setOffset((value) => value + PAGE_SIZE)}
            >
              Siguiente
            </Button>
          </div>
        </section>
      )}

      {selectedId && (
        <RequestDetailModal onClose={closeDetail}>
          {actionNotice && (
            <p className="success-alert" role="status">
              <CheckCircle2 size={18} />
              <span>{actionNotice}</span>
            </p>
          )}
          {detailError && (
            <p className="alert" role="alert">
              {detailError}
            </p>
          )}
          {loadingDetail && !detail && !detailError && (
            <p role="status">Cargando detalle de la contratación…</p>
          )}
          {detail && (
            <section className="client-request-detail" aria-label="Detalle de solicitud seleccionada">
                            <div className="client-request-badges" style={{ marginBottom: 12 }}>
                <ModalityBadge modalidad={detail.modalidad} />
                                <StateBadge
                  estado={detail.estado}
                  pendienteRecepcion={
                    resolveReceptionUiState(detail, detail.seguimiento_recepcion).isPendingReceptionBadge
                  }
                  incidenciaAbierta={
                    resolveReceptionUiState(detail, detail.seguimiento_recepcion).isBlockedByIncident
                  }
                />
              </div>

              <h2>{detail.propuesta.servicio_nombre || detail.servicio?.nombre || "Detalle del servicio"}</h2>
              <p className="client-request-detail-sub">
                Fecha programada: <strong>{formatDateTime(detail.fecha_hora)}</strong> · Creada:{" "}
                {formatDateTime(detail.creada_en)}
              </p>

              {/* HU-34 & HU-35: Banner superior sincronizado mediante State Resolver */}
              {resolveReceptionUiState(detail, detail.seguimiento_recepcion).isBlockedByIncident ? (
                <section
                  className="client-request-box"
                  aria-label="Incidencia abierta en revisión administrativa"
                  style={{
                    background: "#fef2f2",
                    borderColor: "#fecaca",
                    color: "#991b1b",
                    marginBottom: "16px",
                  }}
                >
                  
                  {(detail.seguimiento_recepcion as any)?.cliente_reporto_incidencia && (detail.seguimiento_recepcion as any)?.tecnico_reporto_incidencia ? (
                    <>
                      <h3>Disputa bilateral en revisión administrativa</h3>
                      <p>
                        Ambas partes (cliente y técnico) han registrado sus reportes sobre este servicio. La administración está mediando el caso.
                      </p>
                    </>
                  ) : (detail.seguimiento_recepcion as any)?.tecnico_reporto_incidencia ? (
                    <>
                      <h3>Incidencia o justificación reportada por el técnico</h3>
                      <p>
                        El técnico registró un reporte sobre este servicio. El cierre automático está pausado. Puedes registrar tu versión o descargo a continuación.
                      </p>
                      {detail.seguimiento_recepcion?.incidencia?.motivo && (
                        <p style={{ marginTop: "6px" }}>
                          <strong>Motivo manifestado por el técnico:</strong> {detail.seguimiento_recepcion.incidencia.motivo}
                        </p>
                      )}
                    </>
                  ) : (
                    <>
                      <h3>Incidencia reportada por ti · Cierre automático detenido</h3>
                      <p>
                        Has abierto una incidencia sobre este servicio. Se encuentra en revisión administrativa.
                      </p>
                      {detail.seguimiento_recepcion?.incidencia?.motivo && (
                        <p style={{ marginTop: "6px" }}>
                          <strong>Motivo reportado:</strong> {detail.seguimiento_recepcion.incidencia.motivo}
                        </p>
                      )}
                    </>
                  )}
                </section>
              ) : (
                resolveReceptionUiState(detail, detail.seguimiento_recepcion).showConfirmationCountdown && (
                  <section
                    className="client-request-box"
                    aria-label="Plazo de confirmación de trabajo finalizado"
                    style={{
                      background: "#eff6ff",
                      borderColor: "#bfdbfe",
                      color: "#1e3a8a",
                      marginBottom: "16px",
                    }}
                  >
                    <h3>Trabajo reportado como finalizado · Pendiente de recepción</h3>
                    <p>
                      El técnico informó que terminó el servicio. La contratación se conserva{" "}
                      <strong>En proceso (pendiente de recepción)</strong> y no libera fondos de inmediato.
                      Tienes un plazo máximo de <strong>48 horas</strong> para confirmar la finalización o
                      abrir una incidencia.
                    </p>
                    <p>
                      <strong>Finalización reportada:</strong>{" "}
                      {formatTegucigalpaDateTime(
                        detail.finalizacion_reportada_local || detail.finalizacion_reportada_en,
                      )}
                    </p>
                    <p>
                      <strong>Vencimiento de confirmación (48 h):</strong>{" "}
                      {formatTegucigalpaDateTime(
                        detail.vencimiento_confirmacion_local || detail.vencimiento_confirmacion_en,
                      )}
                    </p>
                    <p>
                      <strong>
                        {
                          formatConfirmationWindowStatus(
                            detail.vencimiento_confirmacion_local || detail.vencimiento_confirmacion_en,
                          ).etiqueta
                        }
                      </strong>
                    </p>
                  </section>
                )
              )}

              <div className="client-request-detail-grid">
                {/* CA-03: Técnico seleccionado cuando existe */}
                <section className="client-request-box" aria-label="Técnico seleccionado">
                  <h3>Técnico seleccionado</h3>
                  {detail.tecnico && detail.tecnico.nombre_completo ? (
                    <>
                      <p className="client-request-tech-name">{detail.tecnico.nombre_completo}</p>
                      <p>
                        Estado:{" "}
                        {detail.tecnico.estado_verificacion === "VERIFICADO"
                          ? "Técnico verificado"
                          : "Registrado en TekniGO"}
                        {detail.tecnico.calificacion_promedio != null &&
                          ` · Calificación: ${detail.tecnico.calificacion_promedio.toFixed(1)} ★`}
                      </p>
                      {detail.tecnico.usuario_id && (
                        <Link
                          className="client-request-tech-link"
                          to={`/tecnicos/${detail.tecnico.usuario_id}`}
                        >
                          Ver perfil público <ArrowUpRight size={14} />
                        </Link>
                      )}
                    </>
                  ) : (
                    <p>Aún no se ha seleccionado un técnico para esta solicitud.</p>
                  )}
                </section>

                {/* CA-03: Propuesta pactada */}
                <section className="client-request-box" aria-label="Propuesta pactada">
                  <h3>Propuesta pactada</h3>
                  <dl className="client-request-proposal-list">
                    <div>
                      <dt>Monto pactado</dt>
                      <dd className="client-request-total">
                        {formatLempiras(detail.propuesta.monto_pactado)}
                      </dd>
                    </div>
                    {Number((detail as any).monto_ajuste || 0) !== 0 && (
                      <div>
                        <dt>Monto base original</dt>
                        <dd>{formatLempiras(detail.monto_base)}</dd>
                      </div>
                    )}
                    {detail.propuesta.tarifa_aplicada != null && (
                      <div>
                        <dt>Tarifa aplicada</dt>
                        <dd>
                          {formatLempiras(detail.propuesta.tarifa_aplicada)}{" "}
                          {formatUnitLabel(detail.propuesta.unidad_cobro)}
                        </dd>
                      </div>
                    )}
                    {detail.propuesta.cantidad != null && (
                      <div>
                        <dt>Cantidad</dt>
                        <dd>{detail.propuesta.cantidad}</dd>
                      </div>
                    )}
                    {detail.propuesta.metodo_pago && (
                      <div>
                        <dt>Método de pago</dt>
                        <dd style={{ textTransform: "capitalize" }}>{detail.propuesta.metodo_pago}</dd>
                      </div>
                    )}
                  </dl>
                </section>
              </div>

              <section className="client-request-box" aria-label="Ubicación y descripción">
                <h3>Ubicación y descripción del trabajo</h3>
                <p>
                  <strong>Ubicación:</strong>{" "}
                  {[detail.municipio, detail.sector, detail.direccion_exacta].filter(Boolean).join(", ") ||
                    "No especificada"}
                </p>
                {detail.descripcion && (
                  <p>
                    <strong>Descripción:</strong> {detail.descripcion}
                  </p>
                )}
              </section>

              {/* HU-28 CA-05: Propuestas recibidas en solicitudes abiertas */}
              {detail.modalidad === "ABIERTA" && (
                <section className="client-request-box" aria-label="Propuestas recibidas">
                  <h3 style={{ margin: "0 0 0.75rem" }}>Propuestas recibidas ({detail.propuestas_recibidas?.length || 0})</h3>

                  {isOffline && (
                    <div className="alert" role="status" style={{ marginBottom: "1rem" }}>
                      <WifiOff size={16} />
                      <span>Sin conexión a internet. Los datos se sincronizarán automáticamente al reconectar.</span>
                    </div>
                  )}
                  {!detail.propuestas_recibidas || detail.propuestas_recibidas.length === 0 ? (
                    <p>Aún no has recibido propuestas de técnicos para esta licitación.</p>
                  ) : (
                    <div className="client-requests-list">
                                                                  {detail.propuestas_recibidas.map((prop) => (
                        <div
                          key={prop.id}
                          style={{
                            background: "#fff",
                            border: "1px solid #e2e8f0",
                            borderRadius: "10px",
                            padding: "1rem 1.25rem",
                            marginBottom: "0.85rem",
                            boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
                          }}
                        >
                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "flex-start",
                              flexWrap: "wrap",
                              gap: "12px",
                            }}
                          >
                            {/* Información del técnico e insignias (CA-02) */}
                            <div style={{ flex: "1 1 280px" }}>
                              <div
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "8px",
                                  flexWrap: "wrap",
                                  marginBottom: "4px",
                                }}
                              >
                                <strong style={{ fontSize: "1.05rem" }}>
                                  {prop.tecnico?.nombre_completo || "Técnico postulado"}
                                </strong>

                                {/* Insignia 1: Identidad Verificada */}
                                {prop.tecnico?.verificado && (
                                  <span
                                    style={{
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: "4px",
                                      fontSize: "0.75rem",
                                      padding: "2px 8px",
                                      borderRadius: "12px",
                                      fontWeight: 600,
                                      background: "#f0fdf4",
                                      color: "#166534",
                                      border: "1px solid #bbf7d0",
                                    }}
                                    title="Identidad verificada con DNI y antecedentes"
                                  >
                                    <ShieldCheck size={13} />
                                    Verificado
                                  </span>
                                )}

                                {/* Insignia 2: Certificación de Oficio Separada */}
                                {prop.tecnico?.certificado && (
                                  <span
                                    style={{
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: "4px",
                                      fontSize: "0.75rem",
                                      padding: "2px 8px",
                                      borderRadius: "12px",
                                      fontWeight: 600,
                                      background: "#eff6ff",
                                      color: "#1d4ed8",
                                      border: "1px solid #bfdbfe",
                                    }}
                                    title={`Certificación de oficio aprobada (${prop.tecnico.certificaciones_aprobadas_count} cert.)`}
                                  >
                                    <Award size={13} />
                                    Certificado de oficio
                                  </span>
                                )}
                              </div>

                              {/* Calificación disponible (CA-02) */}
                              <div
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "6px",
                                  fontSize: "0.85rem",
                                  color: "var(--muted)",
                                  marginBottom: "6px",
                                }}
                              >
                                {prop.tecnico?.calificacion_promedio != null ? (
                                  <span
                                    style={{
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: "3px",
                                      color: "#b45309",
                                      fontWeight: 600,
                                    }}
                                  >
                                    <Star size={14} fill="#f59e0b" color="#f59e0b" />
                                    {prop.tecnico.calificacion_promedio.toFixed(1)} / 5.0
                                  </span>
                                ) : (
                                  <span>Sin calificaciones previas</span>
                                )}
                                <span>•</span>
                                <span>Enviada: {formatDateTime(prop.creada_en)}</span>
                              </div>

                              {/* Tipo de propuesta y justificación */}
                              <div style={{ marginTop: "4px" }}>
                                <span
                                  style={{
                                    display: "inline-block",
                                    fontSize: "0.75rem",
                                    padding: "2px 8px",
                                    borderRadius: "6px",
                                    fontWeight: 600,
                                    background: prop.tipo === "CONTRAOFERTA" ? "#fef3c7" : "#f1f5f9",
                                    color: prop.tipo === "CONTRAOFERTA" ? "#92400e" : "#475569",
                                    border: `1px solid ${prop.tipo === "CONTRAOFERTA" ? "#fde68a" : "#e2e8f0"}`,
                                  }}
                                >
                                  {prop.tipo === "CONTRAOFERTA" ? "Contraoferta propuesta" : "Aceptó presupuesto publicado"}
                                </span>
                                {prop.justificacion && (
                                  <p style={{ margin: "4px 0 0", fontSize: "0.85rem", fontStyle: "italic", color: "#334155" }}>
                                    "{prop.justificacion}"
                                  </p>
                                )}
                              </div>
                            </div>

                            {/* Monto propuesto, estado y enlace al perfil (CA-02, CA-06) */}
                            <div
                              style={{
                                textAlign: "right",
                                display: "flex",
                                flexDirection: "column",
                                alignItems: "flex-end",
                                gap: "6px",
                              }}
                            >
                              <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Monto propuesto</span>
                              <strong style={{ fontSize: "1.25rem", color: "var(--navy)" }}>
                                {formatLempiras(prop.monto)}
                              </strong>

                                                            {/* Indicador de habilitación y estado (CA-06) */}
                              {prop.estado === "ACEPTADA" ? (
                                <span style={{ fontSize: "0.8rem", color: "#15803d", fontWeight: 700 }}>
                                  Propuesta adjudicada ✓
                                </span>
                              ) : prop.estado === "RECHAZADA" ? (
                                <span style={{ fontSize: "0.8rem", color: "#b91c1c", fontWeight: 600 }}>
                                  Propuesta descartada / rechazada
                                </span>
                              ) : prop.tecnico && !prop.tecnico.habilitado_operativamente ? (
                                <span style={{ fontSize: "0.8rem", color: "#b45309", fontWeight: 600 }}>
                                  Técnico no disponible
                                </span>
                              ) : (
                                <span style={{ fontSize: "0.8rem", color: "#15803d", fontWeight: 600 }}>
                                  Disponible para adjudicar
                                </span>
                              )}

                              {confirmAwardProp?.id === prop.id ? (
                                <div
                                  style={{
                                    marginTop: "8px",
                                    padding: "10px 12px",
                                    borderRadius: "8px",
                                    background: "#f0fdf4",
                                    border: "1px solid #bbf7d0",
                                    textAlign: "left",
                                    maxWidth: "360px",
                                  }}
                                >
                                  <p style={{ margin: "0 0 8px", fontSize: "0.85rem", color: "#14532d", fontWeight: 600 }}>
                                    Confirmar adjudicación a {prop.tecnico?.nombre_completo || "este técnico"} por{" "}
                                    {formatLempiras(prop.monto)}
                                  </p>
                                  <label
                                    style={{
                                      display: "block",
                                      fontSize: "0.8rem",
                                      color: "#166534",
                                      marginBottom: "8px",
                                    }}
                                  >
                                    Método de pago:
                                    <select
                                      value={awardPaymentMethod}
                                      disabled={Boolean(awardingProposalId)}
                                      onChange={(e) =>
                                        setAwardPaymentMethod(e.target.value as "efectivo" | "tarjeta")
                                      }
                                      style={{
                                        marginLeft: "8px",
                                        padding: "3px 8px",
                                        borderRadius: "6px",
                                        border: "1px solid #86efac",
                                        fontSize: "0.8rem",
                                      }}
                                    >
                                      <option value="efectivo">Efectivo (pasa a Aceptada al instante)</option>
                                      <option value="tarjeta">Tarjeta (requiere cobro en pasarela)</option>
                                    </select>
                                  </label>
                                  <p style={{ margin: "0 0 8px", fontSize: "0.75rem", color: "#15803d" }}>
                                    Las demás propuestas pendientes se descartarán y solo este técnico recibirá tu dirección exacta y teléfono.
                                  </p>
                                  <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
                                    <button
                                      type="button"
                                      disabled={Boolean(awardingProposalId)}
                                      onClick={() => setConfirmAwardProp(null)}
                                      style={{
                                        background: "transparent",
                                        border: "1px solid #cbd5e1",
                                        borderRadius: "6px",
                                        padding: "4px 10px",
                                        fontSize: "0.8rem",
                                        cursor: "pointer",
                                      }}
                                    >
                                      Cancelar
                                    </button>
                                    <button
                                      type="button"
                                      disabled={Boolean(awardingProposalId)}
                                      onClick={handleConfirmAwardClick}
                                      style={{
                                        background: "#16a34a",
                                        color: "#fff",
                                        border: "none",
                                        borderRadius: "6px",
                                        padding: "4px 12px",
                                        fontSize: "0.8rem",
                                        fontWeight: 600,
                                        cursor: "pointer",
                                      }}
                                    >
                                      {awardingProposalId === prop.id
                                        ? "Adjudicando…"
                                        : awardPaymentMethod === "tarjeta"
                                          ? "Continuar al pago"
                                          : "Confirmar adjudicación"}
                                    </button>
                                  </div>
                                </div>
                              ) : confirmRejectId === prop.id ? (
                                <div
                                  style={{
                                    marginTop: "8px",
                                    padding: "8px 12px",
                                    borderRadius: "8px",
                                    background: "#fef2f2",
                                    border: "1px solid #fecaca",
                                    textAlign: "left",
                                    maxWidth: "340px",
                                  }}
                                >
                                  <p style={{ margin: "0 0 8px", fontSize: "0.85rem", color: "#991b1b", fontWeight: 500 }}>
                                    ¿Rechazar propuesta de <strong>{prop.tecnico?.nombre_completo || "este técnico"}</strong>? La licitación seguirá activa para otras ofertas.
                                  </p>
                                  <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
                                    <button
                                      type="button"
                                      disabled={rejectingProposalId === prop.id}
                                      onClick={() => setConfirmRejectId(null)}
                                      style={{
                                        background: "transparent",
                                        border: "1px solid #cbd5e1",
                                        borderRadius: "6px",
                                        padding: "4px 10px",
                                        fontSize: "0.8rem",
                                        cursor: "pointer",
                                      }}
                                    >
                                      Cancelar
                                    </button>
                                    <button
                                      type="button"
                                      disabled={rejectingProposalId === prop.id}
                                      onClick={() => executeRejectProposal(prop.id)}
                                      style={{
                                        background: "#dc2626",
                                        color: "#fff",
                                        border: "none",
                                        borderRadius: "6px",
                                        padding: "4px 12px",
                                        fontSize: "0.8rem",
                                        fontWeight: 600,
                                        cursor: "pointer",
                                      }}
                                    >
                                      {rejectingProposalId === prop.id ? "Rechazando…" : "Sí, rechazar"}
                                    </button>
                                  </div>
                                </div>
                              ) : (
                                <div
                                  style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: "8px",
                                    flexWrap: "wrap",
                                    marginTop: "6px",
                                    justifyContent: "flex-end",
                                  }}
                                >
                                  {prop.tecnico?.usuario_id && (
                                    <Link
                                      className="button button-outline"
                                      to={`/tecnicos/${prop.tecnico.usuario_id}`}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      style={{
                                        width: "auto",
                                        fontSize: "0.8rem",
                                        padding: "4px 10px",
                                        display: "inline-flex",
                                        alignItems: "center",
                                        gap: "4px",
                                      }}
                                    >
                                      Ver perfil completo <ArrowUpRight size={13} />
                                    </Link>
                                  )}
                                  {prop.estado === "PENDIENTE" &&
                                    ["CON OFERTAS", "CON_OFERTAS"].includes(
                                      (detail.estado || "").toUpperCase()
                                    ) &&
                                    prop.tecnico?.habilitado_operativamente !== false && (
                                      <Button
                                        type="button"
                                        style={{
                                          width: "auto",
                                          fontSize: "0.8rem",
                                          padding: "4px 12px",
                                          background: "#16a34a",
                                          color: "#fff",
                                          display: "inline-flex",
                                          alignItems: "center",
                                          gap: "4px",
                                        }}
                                        onClick={() => startAwardConfirmation(prop)}
                                      >
                                        <UserCheck size={13} />
                                        Seleccionar técnico
                                      </Button>
                                    )}
                                  {prop.estado === "PENDIENTE" &&
                                    ["PUBLICADA", "CON OFERTAS", "CON_OFERTAS"].includes(
                                      (detail.estado || "").toUpperCase()
                                    ) && (
                                      <Button
                                        type="button"
                                        className="button-outline"
                                        style={{
                                          width: "auto",
                                          fontSize: "0.8rem",
                                          padding: "4px 10px",
                                          color: "#dc2626",
                                          borderColor: "#fca5a5",
                                          display: "inline-flex",
                                          alignItems: "center",
                                          gap: "4px",
                                        }}
                                        onClick={() => {
                                          setConfirmAwardProp(null);
                                          setConfirmRejectId(prop.id);
                                        }}
                                      >
                                        <X size={13} />
                                        Rechazar propuesta
                                      </Button>
                                    )}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              )}

              {/* HU-35: Confirmación de recepción, plazo de 48h (America/Tegucigalpa) e incidencias */}
              {(detail.seguimiento_recepcion?.finalizacion_reportada ||
                detail.seguimiento_recepcion?.incidencia_abierta ||
                ["EN PROCESO", "EN_PROCESO", "COMPLETADA"].includes(
                  (detail.estado || "").toUpperCase(),
                )) && (
                <section
                  className="client-request-box"
                  aria-label="Confirmación de recepción del servicio"
                >
                  <h3>Recepción del trabajo y cierre del acuerdo</h3>
                  {detail.seguimiento_recepcion?.finalizacion_reportada_tegucigalpa && (
                    <p style={{ fontSize: "0.88rem", color: "#334155", marginBottom: "6px" }}>
                      <strong>Finalización reportada por el técnico:</strong>{" "}
                      {formatDateTimeTegucigalpa(
                        detail.seguimiento_recepcion.finalizacion_reportada_tegucigalpa,
                      )}
                    </p>
                  )}
                    {detail.seguimiento_recepcion?.plazo_confirmacion_vence_tegucigalpa &&
                    !detail.seguimiento_recepcion?.completada &&
                    !detail.seguimiento_recepcion?.incidencia_abierta && (
                      <p style={{ fontSize: "0.88rem", color: "#0369a1", marginBottom: "8px" }}>
                        <strong>Plazo de 48 horas para confirmar o abrir incidencia:</strong> vence el{" "}
                        {formatDateTimeTegucigalpa(
                          detail.seguimiento_recepcion.plazo_confirmacion_vence_tegucigalpa,
                        )}
                      </p>
                    )}

                  {detail.seguimiento_recepcion?.incidencia_abierta && (
                    <div
                      role="alert"
                      style={{
                        background: "#fef2f2",
                        border: "1px solid #fecaca",
                        borderRadius: "8px",
                        padding: "10px 12px",
                        marginBottom: "10px",
                      }}
                    >
                      <strong style={{ color: "#b91c1c", display: "block" }}>
                        Incidencia abierta — Cierre automático y liberación de fondos detenidos
                      </strong>
                      <span style={{ fontSize: "0.85rem", color: "#7f1d1d" }}>
                        Motivo: {detail.seguimiento_recepcion.incidencia?.motivo}
                      </span>
                    </div>
                  )}

                  {detail.seguimiento_recepcion?.completada ||
                  (detail.estado || "").toUpperCase() === "COMPLETADA" ? (
                    <div
                      style={{
                        background: "#f0fdf4",
                        border: "1px solid #86efac",
                        borderRadius: "8px",
                        padding: "12px",
                      }}
                    >
                      <strong style={{ color: "#15803d", display: "block", marginBottom: "4px" }}>
                        Acuerdo completado e inmutable
                      </strong>
                      <p style={{ fontSize: "0.85rem", color: "#166534", margin: "2px 0" }}>
                        <strong>Autor del cierre:</strong>{" "}
                        {detail.seguimiento_recepcion?.autor_cierre || "CLIENTE"}
                        {detail.seguimiento_recepcion?.fecha_cierre_tegucigalpa
                          ? ` — ${formatDateTimeTegucigalpa(detail.seguimiento_recepcion.fecha_cierre_tegucigalpa)}`
                          : ""}
                      </p>
                      {detail.seguimiento_recepcion?.efecto_financiero && (
                        <p style={{ fontSize: "0.84rem", color: "#166534", margin: "4px 0" }}>
                          <strong>Efecto financiero único:</strong>{" "}
                          {detail.seguimiento_recepcion.efecto_financiero.tipo_efecto} — Comisión:{" "}
                          {formatLempiras(
                            detail.seguimiento_recepcion.efecto_financiero.monto_comision,
                          )}
                        </p>
                      )}
                      {detail.seguimiento_recepcion?.plazo_resena_vence_tegucigalpa && (
                        <p style={{ fontSize: "0.84rem", color: "#0f766e", margin: "4px 0 0" }}>
                          <strong>Plazo independiente de 48h para reseña:</strong> habilitado hasta el{" "}
                          {formatDateTimeTegucigalpa(
                            detail.seguimiento_recepcion.plazo_resena_vence_tegucigalpa,
                          )}
                        </p>
                      )}
                    </div>
                    ) : !detail.seguimiento_recepcion?.incidencia_abierta &&
                    !(
                      detail.seguimiento_recepcion?.finalizacion_reportada ||
                      detail.pendiente_recepcion ||
                      detail.finalizacion_reportada_en ||
                      detail.finalizacion_reportada_local
                    ) ? (
                    <p style={{ fontSize: "0.88rem", color: "#475569", margin: "4px 0 0" }}>
                      El técnico inició la ejecución del servicio. Los botones para{" "}
                      <strong>confirmar recepción</strong> o <strong>reportar incidencia</strong> se
                      habilitarán automáticamente en cuanto el técnico marque el trabajo como finalizado.
                    </p>
                  ) : (
                    (detail.seguimiento_recepcion?.incidencia_abierta ||
                      ["EN PROCESO", "EN_PROCESO"].includes((detail.estado || "").toUpperCase())) && (
                      <div style={{ marginTop: "8px" }}>
                        {/* Si no hay incidencia abierta, se puede confirmar normalmente o reportar */}
                        {!detail.seguimiento_recepcion?.incidencia_abierta ? (
                          <>
                            <label
                              style={{
                                display: "block",
                                fontSize: "0.84rem",
                                fontWeight: 600,
                                marginBottom: "4px",
                              }}
                            >
                              Comentario de recepción (opcional)
                            </label>
                            <input
                              type="text"
                              value={receptionComment}
                              onChange={(e) => setReceptionComment(e.target.value)}
                              placeholder="Ej. Trabajo recibido conforme a lo acordado"
                              style={{
                                width: "100%",
                                padding: "8px 10px",
                                borderRadius: "6px",
                                border: "1px solid #cbd5e1",
                                marginBottom: "10px",
                                fontSize: "0.88rem",
                              }}
                            />
                            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                              <Button
                                type="button"
                                style={{ width: "auto", background: "#16a34a", color: "#fff" }}
                                disabled={confirmingReception}
                                onClick={handleConfirmReception}
                              >
                                {confirmingReception
                                  ? "Confirmando recepción…"
                                  : "Confirmar recepción del trabajo"}
                              </Button>
                              <Button
                                type="button"
                                className="button-outline"
                                disabled={confirmingReception}
                                onClick={() => setShowIncidentModal(true)}
                              >
                                Reportar incidencia o disputa
                              </Button>
                            </div>
                          </>
                        ) : (
                          /* Si ya hay una incidencia abierta (por el técnico o por el cliente) */
                          <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                          {/* Si la abrió el técnico y el cliente aún no ha dado su versión */}
                          {(detail.seguimiento_recepcion as any)?.tecnico_reporto_incidencia && !(detail.seguimiento_recepcion as any)?.cliente_reporto_incidencia ? (
                            <Button
                              type="button"
                              className="button-outline"
                              style={{ color: "#dc2626", borderColor: "#fca5a5", fontWeight: 700 }}
                              onClick={() => setShowIncidentModal(true)}
                            >
                              <AlertTriangle size={15} /> Registrar tu versión o descargo
                            </Button>
                          ) : (
                            <Button type="button" className="button-outline" disabled style={{ fontSize: "12px" }}>
                              <CheckCircle2 size={14} color="#16a34a" /> Tu reporte está registrado y en mediación
                            </Button>
                          )}
                          </div>
                        )}
                      </div>
                    )
                  )}
                </section>
                
              )}
              
              {!detail.incidencia_abierta && (
                <AdjustmentPanel
                  user={user} solicitudId={detail.id} rol="CLIENTE" estado={detail.estado}
                  montoBase={String((detail as any).monto_total ?? detail.monto_base ?? "")}
                  onChanged={() => setRefreshTick((v) => v + 1)}
                />
              )}
              {(detail.estado || "").toUpperCase() === "CANCELADA" && (
                <CancellationNotice user={user} solicitudId={detail.id} rol="CLIENTE"
                  onChanged={() => setRefreshTick((v) => v + 1)} />
              )}
              <RequestTimeline items={detail.linea_tiempo} />

              {/* CA-04: Acciones permitidas según situación */}
              <section className="client-request-box" aria-label="Acciones disponibles">
                <h3>Acciones disponibles para esta solicitud</h3>
                {detail.acciones_permitidas.length === 0 ? (
                  <p className="client-request-no-actions">
                    Esta solicitud se encuentra en estado{" "}
                    <strong>{formatRequestStateLabel(detail.estado).toLowerCase()}</strong> y no admite
                    modificaciones ni acciones adicionales.
                  </p>
                ) : (
                  <div className="client-request-actions">
                    {detail.acciones_permitidas.map((accion) => {
                      const info = ACTION_LABELS[accion] || {
                        label: accion,
                        hint: "Acción permitida para el estado actual de la solicitud.",
                      };
                      return (
                        <div key={accion} className="client-request-action-row">
                          <div>
                            <strong>{info.label}</strong>
                            <p>{info.hint}</p>
                          </div>
                          <Button
                            type="button"
                            className={accion === "CANCELAR" ? "button-outline" : ""}
                            style={{ width: "auto" }}
                            disabled={Boolean(executingAction)}
                            onClick={() => handleAction(accion)}
                          >
                            {executingAction === accion ? "Procesando…" : info.label}
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                )}
                {isChatEnabledForState(detail.estado) && (
                  <ContractChatPanel
                    user={user}
                    modalidad={detail.modalidad === "ABIERTA" ? "ABIERTA" : "DIRECTA"}
                    solicitudId={detail.id}
                    estadoSolicitud={detail.estado}
                    currentRole="CLIENTE"
                    counterpartName={detail.tecnico?.nombre_completo || "Técnico asignado"}
                    onChatClosed={() => {
                      getMyRequestDetail(user, detail.id).then((d) => setDetail(d)).catch(() => {});
                      setRefreshTick((v) => v + 1);
                    }}
                  />
                )}
              </section>

              {detail.estado.toUpperCase() === "COMPLETADA" && detail.tecnico?.nombre_completo && (
                <section className="client-request-box" aria-label="Calificación del técnico">
                  <h3>Calificación del servicio</h3>
                  <p>El servicio ha sido marcado como completado. Puedes evaluar el trabajo realizado por el profesional.</p>
                  <Button
                    type="button"
                    className="button"
                    style={{ width: "auto", display: "inline-flex", alignItems: "center", gap: 6 }}
                    onClick={() =>
                      setRatingTarget({
                        tipo_solicitud: detail.modalidad === "ABIERTA" ? "ABIERTA" : "DIRECTA",
                        solicitud_id: detail.id,
                        evaluadoNombre: detail.tecnico?.nombre_completo || "Técnico",
                      })
                    }
                  >
                    <Star size={16} fill="currentColor" />
                    {reviewedIds[detail.id] ? "Evaluación enviada" : "Calificar técnico"}
                  </Button>
                </section>
              )}
            </section>
          )}
          {showAwardGateway && confirmAwardProp && (
            <SecurePaymentGatewayModal
              monto={confirmAwardProp.monto}
              titulo="Confirmar cobro para adjudicar licitación"
              onSuccess={handleAwardCardSuccess}
              onCancel={() => setShowAwardGateway(false)}
            />
          )}
        </RequestDetailModal>
      )}
      {ratingTarget && (
        <ReviewModal
          user={user}
          tipo_solicitud={ratingTarget.tipo_solicitud}
          solicitud_id={ratingTarget.solicitud_id}
          evaluadoNombre={ratingTarget.evaluadoNombre}
          rolEvaluador="CLIENTE"
          onClose={() => setRatingTarget(null)}
          onSuccess={(res) => {
            setReviewedIds((prev) => ({ ...prev, [ratingTarget.solicitud_id]: true }));
            setActionNotice(res.mensaje);
          }}
        />
      )}
      {detail && (
        <ReportIncidentModal
          isOpen={showIncidentModal}
          onClose={() => setShowIncidentModal(false)}
          onSuccess={(msg) => {
            setActionNotice(msg);
            getMyRequestDetail(user, detail.id).then((d) => setDetail(d));
            setRefreshTick((v) => v + 1);
          }}
          user={user}
          solicitudId={detail.id}
          rol="CLIENTE"
        />
      )}
      {detail && (
        <CancelRequestModal
          isOpen={showCancel}
          onClose={() => setShowCancel(false)}
          user={user}
          solicitudId={detail.id}
          rol="CLIENTE"
          estado={detail.estado}
          onDone={(msg) => { setActionNotice(msg); setRefreshTick((v) => v + 1); }}
        />
      )}
    </div>
  );
}
