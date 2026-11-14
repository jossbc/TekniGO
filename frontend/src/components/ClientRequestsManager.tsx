import { useEffect, useId, useRef, useState } from "react";
import type { User } from "firebase/auth";
import { Link } from "react-router-dom";
import {
  ArrowUpRight,
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
import { Button } from "./ui";
import { errorMessage } from "../lib/api";
import {
  ESTADO_OPTIONS,
  MODALIDAD_OPTIONS,
  executeMyRequestAction,
  formatLempiras,
  formatRequestModalityLabel,
  formatRequestStateLabel,
  formatUnitLabel,
  getMyRequestDetail,
  listMyRequests,
  rejectProposalApi,
  type ClientRequestDetail,
  type ClientRequestsPageResponse,
  type EstadoFiltro,
  type ModalidadFiltro,
} from "../lib/clientRequests";

const PAGE_SIZE = 10;

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "Sin fecha registrada";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("es-HN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function StateBadge({ estado }: { estado: string }) {
  const normalized = (estado || "PENDIENTE").toUpperCase();
  return (
    <span className={`request-status-badge request-status-${normalized.toLowerCase()}`}>
      {formatRequestStateLabel(normalized)}
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
  COMPLETAR: {
    label: "Confirmar finalización",
    hint: "Disponible cuando el trabajo acordado fue realizado.",
  },
};

export default function ClientRequestsManager({
  user,
  initialRequestId = "",
  onSelectRequestId,
}: {
  user: User;
  initialRequestId?: string;
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
  const [actionNotice, setActionNotice] = useState("");
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
    listMyRequests(user, { modalidad, estado, offset, limit: PAGE_SIZE })
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
  }, [user, modalidad, estado, offset, refreshTick]);

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

    const [rejectingProposalId, setRejectingProposalId] = useState<string>("");

  async function handleRejectProposal(proposalId: string, techName: string) {
    if (!detail || rejectingProposalId) return;
    const confirmed = window.confirm(
      `¿Deseas rechazar la propuesta de ${techName}? La licitación seguirá activa para recibir otras ofertas.`
    );
    if (!confirmed) return;

    setRejectingProposalId(proposalId);
    setDetailError("");
    setActionNotice("");
    try {
      const res = await rejectProposalApi(user, detail.id, proposalId);
      setActionNotice(res.mensaje || "Propuesta rechazada correctamente.");
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
              {ESTADO_OPTIONS.map((opt) => (
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
          <h3>No hay solicitudes para mostrar</h3>
          <p>
            {modalidad === "TODOS" && estado === "TODOS"
              ? "Aún no has registrado contrataciones directas ni solicitudes abiertas. Explora el catálogo de técnicos para solicitar un servicio."
              : "No se encontraron solicitudes que coincidan con los filtros seleccionados."}
          </p>
          {modalidad === "TODOS" && estado === "TODOS" ? (
            <Link className="button" to="/catalogo">
              Buscar servicios
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
            <h2>Solicitudes encontradas ({page.total})</h2>
            <span>
              Mostrando {offset + 1}–{Math.min(offset + page.solicitudes.length, page.total)} de {page.total}
            </span>
          </div>

          <div className="client-requests-list">
            {page.solicitudes.map((item) => (
              <article
                key={item.id}
                className={`client-request-item${selectedId === item.id ? " client-request-item-selected" : ""}`}
              >
                <div className="client-request-item-main">
                  <div className="client-request-badges">
                    <ModalityBadge modalidad={item.modalidad} />
                    <StateBadge estado={item.estado} />
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
                  </p>
                </div>

                <div className="client-request-item-side">
                  <div className="client-request-budget">
                    <span>Presupuesto</span>
                    <strong>{formatLempiras(item.presupuesto)}</strong>
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
                </div>
              </article>
            ))}
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
                <StateBadge estado={detail.estado} />
              </div>

              <h2>{detail.propuesta.servicio_nombre || detail.servicio?.nombre || "Detalle del servicio"}</h2>
              <p className="client-request-detail-sub">
                Fecha programada: <strong>{formatDateTime(detail.fecha_hora)}</strong> · Creada:{" "}
                {formatDateTime(detail.creada_en)}
              </p>

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
                                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem", flexWrap: "wrap", gap: "8px" }}>
                    <h3 style={{ margin: 0 }}>Propuestas recibidas ({detail.propuestas_recibidas?.length || 0})</h3>
                    <span style={{ fontSize: "0.8rem", color: "var(--muted)", display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <span
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: "50%",
                          background: isOffline ? "#ef4444" : "#22c55e",
                          display: "inline-block",
                        }}
                      />
                      {isOffline ? "Sin conexión (Reconectando…)" : "Actualización en vivo activa"}
                    </span>
                  </div>

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
                              {prop.estado === "RECHAZADA" ? (
                                <span style={{ fontSize: "0.8rem", color: "#b91c1c", fontWeight: 600 }}>
                                  Propuesta rechazada
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

                              {/* Enlace al perfil público */}
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
                                    marginTop: "4px",
                                    display: "inline-flex",
                                    alignItems: "center",
                                    gap: "4px",
                                  }}
                                >
                                  Ver perfil completo <ArrowUpRight size={13} />
                              {/* Botón de rechazo individual (CA-03) */}
                              {prop.estado === "PENDIENTE" &&
                                ["PUBLICADA", "CON OFERTAS", "CON_OFERTAS"].includes(
                                  (detail.estado || "").toUpperCase()
                                ) && (
                                  <Button
                                    type="button"
                                    className="button-outline"
                                    disabled={rejectingProposalId === prop.id}
                                    style={{
                                      width: "auto",
                                      fontSize: "0.8rem",
                                      padding: "4px 10px",
                                      marginTop: "4px",
                                      color: "#dc2626",
                                      borderColor: "#fca5a5",
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: "4px",
                                    }}
                                    onClick={() =>
                                      handleRejectProposal(
                                        prop.id,
                                        prop.tecnico?.nombre_completo || "este técnico"
                                      )
                                    }
                                  >
                                    <X size={13} />
                                    {rejectingProposalId === prop.id ? "Rechazando…" : "Rechazar propuesta"}
                                  </Button>
                                )}
                                </Link>
                                
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              )}

              {/* CA-03: Línea de tiempo de cambios */}
              <section className="client-request-box" aria-label="Línea de tiempo de cambios">
                <h3>Línea de tiempo de cambios</h3>
                <ol className="request-timeline">
                  {detail.linea_tiempo.map((evento, index) => (
                    <li key={`${evento.estado}-${index}`} className="request-timeline-item">
                      <span className="request-timeline-dot" aria-hidden="true">
                        <CheckCircle2 size={16} />
                      </span>
                      <div className="request-timeline-content">
                        <div className="request-timeline-header">
                          <strong>{formatRequestStateLabel(evento.estado)}</strong>
                          <span>{formatDateTime(evento.fecha)}</span>
                        </div>
                        <p>{evento.descripcion}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              </section>

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
              </section>
            </section>
          )}
        </RequestDetailModal>
      )}
    </div>
  );
}
