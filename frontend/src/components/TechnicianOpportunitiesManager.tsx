import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { User } from "firebase/auth";
import {
  Briefcase,
  Calendar,
  CheckCircle2,
  ClipboardList,
  CreditCard,
  EyeOff,
  FileText,
  Lock,
  MapPin,
  RefreshCw,
  UserRound,
  X,
} from "lucide-react";
import { Button } from "./ui";
import { errorMessage } from "../lib/api";
import { formatLempiras } from "../lib/clientRequests";
import {
  discardOpportunityApi,
  fetchMyTechnicianCoverageZones,
  fetchMyTechnicianSpecialties,
  getOpportunityDetailApi,
  listAvailableOpportunitiesApi,
  type AvailableOpportunitiesResponse,
  type OpportunityStateFilter,
  type SpecialtyOption,
  type TechnicianCoverageZoneItem,
  type TechnicianOpportunityItem,
} from "../lib/openRequests";

const PAGE_SIZE = 10;

const OPPORTUNITY_STATE_OPTIONS: { value: OpportunityStateFilter; label: string }[] = [
  { value: "TODOS", label: "Todos los estados" },
  { value: "PUBLICADA", label: "Publicadas" },
  { value: "CON_OFERTAS", label: "Con ofertas" },
];

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "Sin fecha registrada";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("es-HN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function formatOpportunityStateLabel(estado: string): string {
  const key = (estado || "PUBLICADA").trim().toUpperCase().replace(/\s+/g, "_");
  if (key === "CON_OFERTAS") return "Con ofertas";
  if (key === "PUBLICADA") return "Publicada";
  return estado;
}

function OpportunityStateBadge({ estado }: { estado: string }) {
  const key = (estado || "PUBLICADA").trim().toUpperCase().replace(/\s+/g, "_").toLowerCase();
  return (
    <span className={`request-status-badge request-status-${key}`}>
      {formatOpportunityStateLabel(estado)}
    </span>
  );
}

function OpportunityDetailModal({
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
        <h2 id={titleId}>Detalle de oportunidad</h2>
        <button
          type="button"
          className="dossier-modal-close"
          aria-label="Cerrar detalle de oportunidad"
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

export default function TechnicianOpportunitiesManager({
  user,
  initialRequestId = "",
  onSelectRequestId,
}: {
  user: User;
  initialRequestId?: string;
  onSelectRequestId?: (id: string) => void;
}) {
  const [specialties, setSpecialties] = useState<SpecialtyOption[]>([]);
  const [coverageZones, setCoverageZones] = useState<TechnicianCoverageZoneItem[]>([]);

  const [especialidadId, setEspecialidadId] = useState("");
  const [municipio, setMunicipio] = useState("");
  const [sector, setSector] = useState("");
  const [estado, setEstado] = useState<OpportunityStateFilter>("TODOS");
  const [offset, setOffset] = useState(0);

  const [page, setPage] = useState<AvailableOpportunitiesResponse | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState("");
  const [listNotice, setListNotice] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);
  const [discardingId, setDiscardingId] = useState("");

  const [selectedId, setSelectedId] = useState(initialRequestId);
  const [detail, setDetail] = useState<TechnicianOpportunityItem | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState("");

  useEffect(() => {
    setSelectedId(initialRequestId);
  }, [initialRequestId]);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetchMyTechnicianSpecialties(user).catch(() => []),
      fetchMyTechnicianCoverageZones(user).catch(() => []),
    ]).then(([specs, zones]) => {
      if (!active) return;
      setSpecialties(specs || []);
      setCoverageZones(zones || []);
    });
    return () => {
      active = false;
    };
  }, [user]);

  const availableMunicipios = useMemo(() => {
    const unique = new Set<string>();
    for (const z of coverageZones) {
      if (z.municipio?.trim()) unique.add(z.municipio.trim());
    }
    return Array.from(unique).sort((a, b) => a.localeCompare(b, "es"));
  }, [coverageZones]);

  const availableSectores = useMemo(() => {
    const unique = new Set<string>();
    for (const z of coverageZones) {
      if (municipio && z.municipio.trim().toLowerCase() !== municipio.trim().toLowerCase()) {
        continue;
      }
      if (z.sector?.trim()) unique.add(z.sector.trim());
    }
    return Array.from(unique).sort((a, b) => a.localeCompare(b, "es"));
  }, [coverageZones, municipio]);

  useEffect(() => {
    let active = true;
    setLoadingList(true);
    setListError("");
    listAvailableOpportunitiesApi(user, {
      especialidad_id: especialidadId || undefined,
      municipio: municipio || undefined,
      sector: sector || undefined,
      estado,
      offset,
      limit: PAGE_SIZE,
    })
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
  }, [user, especialidadId, municipio, sector, estado, offset, refreshTick]);

  useEffect(() => {
    let active = true;
    setDetail(null);
    setDetailError("");
    if (!selectedId) {
      setLoadingDetail(false);
      return;
    }
    setLoadingDetail(true);
    getOpportunityDetailApi(user, selectedId)
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

  function openDetail(id: string) {
    setListNotice("");
    setSelectedId(id);
    onSelectRequestId?.(id);
  }

  function closeDetail() {
    setSelectedId("");
    onSelectRequestId?.("");
  }

  function clearFilters() {
    setEspecialidadId("");
    setMunicipio("");
    setSector("");
    setEstado("TODOS");
    setOffset(0);
  }

  async function handleDiscard(solicitudId: string, fromModal = false) {
    if (discardingId) return;
    setDiscardingId(solicitudId);
    setListError("");
    setDetailError("");
    setListNotice("");
    try {
      const response = await discardOpportunityApi(user, solicitudId);
      setListNotice(response.mensaje || "Publicación descartada de tu listado personal.");
      if (fromModal || selectedId === solicitudId) {
        closeDetail();
      }
      setRefreshTick((v) => v + 1);
    } catch (err) {
      if (fromModal) {
        setDetailError(errorMessage(err));
      } else {
        setListError(errorMessage(err));
      }
    } finally {
      setDiscardingId("");
    }
  }

  const hasActiveFilters = Boolean(especialidadId || municipio || sector || estado !== "TODOS");

  return (
    <div className="client-requests-section">
      {/* Barra de filtros por oficio, cobertura y estado (ST-01, CA-01, CA-06) */}
      <div className="client-requests-toolbar">
        <div className="client-requests-filter-controls" style={{ flexWrap: "wrap", gap: "0.75rem" }}>
          <label className="client-requests-state-filter">
            <span>Oficio / Especialidad</span>
            <select
              aria-label="Filtrar por oficio"
              value={especialidadId}
              onChange={(event) => {
                setEspecialidadId(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">Todos mis oficios</option>
              {specialties.map((spec) => (
                <option key={spec.id} value={spec.id}>
                  {spec.nombre}
                </option>
              ))}
            </select>
          </label>

          <label className="client-requests-state-filter">
            <span>Municipio</span>
            <select
              aria-label="Filtrar por municipio"
              value={municipio}
              onChange={(event) => {
                setMunicipio(event.target.value);
                setSector("");
                setOffset(0);
              }}
            >
              <option value="">Todos mis municipios</option>
              {availableMunicipios.map((muni) => (
                <option key={muni} value={muni}>
                  {muni}
                </option>
              ))}
            </select>
          </label>

          <label className="client-requests-state-filter">
            <span>Sector</span>
            <select
              aria-label="Filtrar por sector"
              value={sector}
              onChange={(event) => {
                setSector(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">Todos mis sectores</option>
              {availableSectores.map((sec) => (
                <option key={sec} value={sec}>
                  {sec}
                </option>
              ))}
            </select>
          </label>

          <label className="client-requests-state-filter">
            <span>Estado</span>
            <select
              aria-label="Filtrar por estado"
              value={estado}
              onChange={(event) => {
                setEstado(event.target.value as OpportunityStateFilter);
                setOffset(0);
              }}
            >
              {OPPORTUNITY_STATE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>

          <Button
            type="button"
            className="button-outline client-requests-refresh"
            onClick={() => setRefreshTick((v) => v + 1)}
          >
            <RefreshCw size={15} />
            Actualizar
          </Button>
        </div>
      </div>

      {listNotice && (
        <div className="success-alert" role="status">
          <CheckCircle2 size={18} />
          <span>{listNotice}</span>
        </div>
      )}

      {listError && (
        <div className="alert" role="alert">
          {listError}
        </div>
      )}

      {loadingList && !page && <p role="status">Buscando oportunidades compatibles con tu perfil…</p>}

      {/* CA-06: Ausencia de coincidencias muestra mensaje y permite revisar filtros */}
      {page && page.solicitudes.length === 0 && !listError && (
        <section className="catalog-empty client-requests-empty" role="status">
          <ClipboardList size={36} aria-hidden="true" />
          <h3>No se encontraron oportunidades disponibles</h3>
          <p>
            {page.mensaje ||
              "No hay solicitudes abiertas Publicadas o Con ofertas que coincidan con los filtros actuales."}
          </p>
          {hasActiveFilters && (
            <Button type="button" className="button-outline" onClick={clearFilters}>
              Limpiar y revisar filtros
            </Button>
          )}
        </section>
      )}

      {/* Listado de oportunidades (CA-01, CA-03, CA-05) */}
      {page && page.solicitudes.length > 0 && (
        <section className="account-card client-requests-card" aria-label="Oportunidades disponibles">
          <div className="client-requests-card-header">
            <h2>Oportunidades compatibles ({page.total})</h2>
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
                    <span className="request-modality-badge request-modality-abierta">
                      <Briefcase size={13} aria-hidden="true" />{" "}
                      {item.especialidad?.nombre || "Especialidad"}
                    </span>
                    <OpportunityStateBadge estado={item.estado} />
                  </div>

                  <h3>{item.especialidad?.nombre || "Solicitud abierta"}</h3>
                  <p style={{ margin: "0.35rem 0 0.5rem" }}>{item.descripcion}</p>

                  <p className="client-request-meta">
                    <span>
                      <Calendar size={14} aria-hidden="true" /> Fecha requerida:{" "}
                      {formatDateTime(item.fecha_hora)}
                    </span>
                    <span>
                      <MapPin size={14} aria-hidden="true" /> {item.municipio}, {item.sector}
                    </span>
                    <span>
                      <Lock size={14} aria-hidden="true" /> Dirección exacta y teléfono ocultos
                    </span>
                  </p>
                </div>

                <div className="client-request-item-side">
                  <div className="client-request-budget">
                    <span>Presupuesto ofrecido</span>
                    <strong>{formatLempiras(item.presupuesto)}</strong>
                  </div>

                  <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", justifyContent: "flex-end" }}>
                    <Button
                      type="button"
                      className="button-outline"
                      aria-label={`Ver detalle de ${item.especialidad?.nombre || "oportunidad"}`}
                      onClick={() => openDetail(item.id)}
                    >
                      <FileText size={15} />
                      Ver detalle
                    </Button>
                    <Button
                      type="button"
                      className="button-outline"
                      disabled={discardingId === item.id}
                      aria-label={`Descartar publicación de ${item.especialidad?.nombre || "oportunidad"}`}
                      onClick={() => handleDiscard(item.id, false)}
                    >
                      <EyeOff size={15} />
                      {discardingId === item.id ? "Descartando…" : "Descartar"}
                    </Button>
                  </div>
                </div>
              </article>
            ))}
          </div>

          <div className="admin-toolbar admin-pagination">
            <Button
              type="button"
              className="button-outline"
              disabled={offset === 0}
              onClick={() => setOffset((v) => Math.max(0, v - PAGE_SIZE))}
            >
              Anterior
            </Button>
            <Button
              type="button"
              className="button-outline"
              disabled={offset + PAGE_SIZE >= page.total}
              onClick={() => setOffset((v) => v + PAGE_SIZE)}
            >
              Siguiente
            </Button>
          </div>
        </section>
      )}

      {/* Modal de detalle con ubicación parcial y acción de descartar (CA-03, CA-05) */}
      {selectedId && (
        <OpportunityDetailModal onClose={closeDetail}>
          {detailError && (
            <p className="alert" role="alert">
              {detailError}
            </p>
          )}
          {loadingDetail && !detail && !detailError && (
            <p role="status">Cargando detalle de la oportunidad…</p>
          )}
          {detail && (
            <section className="client-request-detail" aria-label="Detalle de oportunidad seleccionada">
              <div className="client-request-badges" style={{ marginBottom: 12 }}>
                <span className="request-modality-badge request-modality-abierta">
                  {detail.especialidad?.nombre || "Licitación abierta"}
                </span>
                <OpportunityStateBadge estado={detail.estado} />
              </div>

              <h2>{detail.especialidad?.nombre || "Oportunidad de trabajo"}</h2>
              <p className="client-request-detail-sub">
                Fecha requerida: <strong>{formatDateTime(detail.fecha_hora)}</strong> · Publicada:{" "}
                {formatDateTime(detail.creada_en)}
              </p>

              <div className="client-request-detail-grid">
                <section className="client-request-box" aria-label="Presupuesto y pago">
                  <h3>Presupuesto del cliente</h3>
                  <dl className="client-request-proposal-list">
                    <div>
                      <dt>Monto ofrecido</dt>
                      <dd className="client-request-total">{formatLempiras(detail.presupuesto)}</dd>
                    </div>
                    <div>
                      <dt>Método de pago</dt>
                      <dd style={{ textTransform: "capitalize" }}>
                        <CreditCard size={14} aria-hidden="true" /> {detail.metodo_pago}
                      </dd>
                    </div>
                  </dl>
                </section>

                <section className="client-request-box" aria-label="Información del cliente y ubicación">
                  <h3>Ubicación parcial y cliente</h3>
                  <p>
                    <UserRound size={14} aria-hidden="true" />{" "}
                    <strong>Cliente:</strong> {detail.cliente?.nombre_completo || "Cliente verificado"}
                  </p>
                  <p>
                    <MapPin size={14} aria-hidden="true" />{" "}
                    <strong>Zona:</strong> {detail.municipio}, {detail.sector}
                  </p>
                  {detail.ubicacion_exacta_protegida ? (
                    <p className="client-request-no-actions" style={{ marginTop: "0.5rem" }}>
                      <Lock size={14} aria-hidden="true" /> La dirección exacta y el teléfono del cliente
                      permanecen ocultos por privacidad hasta que la solicitud sea aceptada.
                    </p>
                  ) : (
                    <>
                      {detail.direccion_exacta && (
                        <p>
                          <strong>Dirección exacta:</strong> {detail.direccion_exacta}
                        </p>
                      )}
                      {detail.cliente?.telefono && (
                        <p>
                          <strong>Teléfono:</strong> {detail.cliente.telefono}
                        </p>
                      )}
                    </>
                  )}
                </section>
              </div>

              <section className="client-request-box" aria-label="Descripción del trabajo">
                <h3>Descripción del trabajo solicitado</h3>
                <p>{detail.descripcion}</p>
              </section>

              <section className="client-request-box" aria-label="Acciones sobre la oportunidad">
                <h3>Acciones sobre esta publicación</h3>
                <div className="client-request-action-row">
                  <div>
                    <strong>Descartar de mi listado</strong>
                    <p>
                      Oculta esta publicación de tu bandeja personal sin cancelarla ni afectar a otros
                      técnicos.
                    </p>
                  </div>
                  <Button
                    type="button"
                    className="button-outline"
                    style={{ width: "auto" }}
                    disabled={discardingId === detail.id}
                    onClick={() => handleDiscard(detail.id, true)}
                  >
                    <EyeOff size={15} />
                    {discardingId === detail.id ? "Descartando…" : "Descartar publicación"}
                  </Button>
                </div>
              </section>
            </section>
          )}
        </OpportunityDetailModal>
      )}
    </div>
  );
}
