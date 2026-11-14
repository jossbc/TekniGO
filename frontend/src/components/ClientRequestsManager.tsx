import { useEffect, useId, useRef, useState } from "react";
import type { User } from "firebase/auth";
import { Link } from "react-router-dom";
import {
  ArrowUpRight,
  Calendar,
  CheckCircle2,
  ClipboardList,
  Clock3,
  FileText,
  MapPin,
  RefreshCw,
  UserCheck,
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
  useEffect(() => {
    if (!detail || detail.modalidad !== "ABIERTA") return;
    const estado = (detail.estado || "").toUpperCase();
    if (estado !== "PUBLICADA" && estado !== "CON OFERTAS" && estado !== "CON_OFERTAS") return;

    const interval = setInterval(() => {
      getMyRequestDetail(user, detail.id)
        .then((updated) => {
          setDetail((prev) => {
            if (!prev || prev.id !== updated.id) return prev;
            const prevCount = prev.propuestas_recibidas?.length || 0;
            const newCount = updated.propuestas_recibidas?.length || 0;
            if (prevCount !== newCount || prev.estado !== updated.estado) {
              return updated;
            }
            return prev;
          });
        })
        .catch(() => {});
    }, 4000);

    return () => clearInterval(interval);
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
                  <h3>Propuestas recibidas ({detail.propuestas_recibidas?.length || 0})</h3>
                  {!detail.propuestas_recibidas || detail.propuestas_recibidas.length === 0 ? (
                    <p>Aún no has recibido propuestas de técnicos para esta licitación.</p>
                  ) : (
                    <div className="client-requests-list">
                                            {detail.propuestas_recibidas.map((prop) => (
                        <div key={prop.id} className="client-request-action-row">
                          <div>
                            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: 4 }}>
                              <strong>{prop.tecnico?.nombre_completo || "Técnico verificado"}</strong>
                              <span
                                style={{
                                  fontSize: "0.75rem",
                                  padding: "2px 8px",
                                  borderRadius: 12,
                                  fontWeight: 600,
                                  background: prop.tipo === "CONTRAOFERTA" ? "#e0f2fe" : "#f0fdf4",
                                  color: prop.tipo === "CONTRAOFERTA" ? "#0369a1" : "#166534",
                                  border: `1px solid ${prop.tipo === "CONTRAOFERTA" ? "#bae6fd" : "#bbf7d0"}`,
                                }}
                              >
                                {prop.tipo === "CONTRAOFERTA" ? "Contraoferta" : "Monto publicado"}
                              </span>
                            </div>
                            <p>
                              Monto propuesto: <strong>{formatLempiras(prop.monto)}</strong> · Estado:{" "}
                              <strong>{formatRequestStateLabel(prop.estado)}</strong> · Enviada:{" "}
                              {formatDateTime(prop.creada_en)}
                            </p>
                            {prop.justificacion && (
                              <p style={{ margin: "4px 0 0", fontStyle: "italic", color: "var(--muted)", fontSize: "0.85rem" }}>
                                Justificación: "{prop.justificacion}"
                              </p>
                            )}
                          </div>
                          {prop.tecnico?.usuario_id && (
                            <Link
                              className="client-request-tech-link"
                              to={`/tecnicos/${prop.tecnico.usuario_id}`}
                            >
                              Ver perfil <ArrowUpRight size={14} />
                            </Link>
                          )}
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
