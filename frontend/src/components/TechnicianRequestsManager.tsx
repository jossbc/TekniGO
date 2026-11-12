import { useState, useEffect } from "react";
import type { User } from "firebase/auth";
import { CheckCircle2, CircleAlert, Clock3, XCircle, Calendar, Inbox, Check, X, RefreshCw } from "lucide-react";
import { errorMessage } from "../lib/api";
import {
  acceptTechnicianDirectRequest,
  listTechnicianDirectRequests,
  rejectTechnicianDirectRequest,
  type TechnicianDirectRequest,
} from "../lib/directRequests";
import LoadingState from "./LoadingState";
import { Button } from "./ui";

export type DirectRequest = TechnicianDirectRequest;

type EstadoFilter = "TODOS" | "PENDIENTE" | "ACEPTADA" | "RECHAZADA";

const estadoConfig: Record<
  string,
  { label: string; bg: string; color: string; border: string; icon: typeof Clock3 }
> = {
  PENDIENTE: { label: "Pendiente", bg: "#fff7ed", color: "#9a3412", border: "#fed7aa", icon: Clock3 },
  ACEPTADA: { label: "Aceptada", bg: "#f0fdf4", color: "#166534", border: "#bbf7d0", icon: CheckCircle2 },
  RECHAZADA: { label: "Rechazada", bg: "#f8fafc", color: "#475569", border: "#cbd5e1", icon: XCircle },
  CANCELADA: { label: "Cancelada", bg: "#f8fafc", color: "#475569", border: "#cbd5e1", icon: XCircle },
  COMPLETADA: { label: "Completada", bg: "#f0fdf4", color: "#166534", border: "#bbf7d0", icon: CheckCircle2 },
};

function normalizeEstado(estado?: string): string {
  return (estado || "PENDIENTE").trim().toUpperCase();
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

export function TechnicianRequestsManager({ user }: { user: User }) {
  const [requests, setRequests] = useState<DirectRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const [filter, setFilter] = useState<EstadoFilter>("TODOS");

  // HU-22: Consultar Solicitudes directas recibidas desde el backend
  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError("");

    listTechnicianDirectRequests(user)
      .then((data) => {
        if (!active) return;
        setRequests(Array.isArray(data) ? data : []);
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
  }, [user, refreshTick]);

  async function handleDecision(id: string, decision: "accept" | "reject") {
    setActionLoading(id);
    setActionError("");
    setActionNotice("");
    const nextState = decision === "accept" ? "ACEPTADA" : "RECHAZADA";
    const successText =
      decision === "accept"
        ? "Solicitud aceptada correctamente."
        : "Solicitud rechazada correctamente.";

    try {
      if (decision === "accept") {
        await acceptTechnicianDirectRequest(user, id);
      } else {
        await rejectTechnicianDirectRequest(user, id);
      }
      setRequests((prev) =>
        prev.map((req) => (req.id === id ? { ...req, estado: nextState } : req)),
      );
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
  const pendingCount = countByState("PENDIENTE");
  const filtered =
    filter === "TODOS"
      ? requests
      : requests.filter((r) => normalizeEstado(r.estado) === filter);

  return (
    <div>
      {/* Encabezado */}
      <div className="client-requests-page-heading" style={{ marginBottom: "24px" }}>
        <div>
          <h1 style={{ fontSize: "clamp(28px, 4vw, 38px)", letterSpacing: "-1.3px", marginBottom: "8px" }}>
            Solicitudes entrantes
          </h1>
          <p style={{ color: "var(--muted)", fontSize: "14px", margin: 0 }}>
            Revisa y gestiona los trabajos que los clientes te han solicitado directamente.
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
            <label htmlFor="filter-estado">Filtrar por estado</label>
            <select
              id="filter-estado"
              value={filter}
              onChange={(e) => setFilter(e.target.value as EstadoFilter)}
            >
              <option value="TODOS">Todas ({requests.length})</option>
              <option value="PENDIENTE">Pendientes ({pendingCount})</option>
              <option value="ACEPTADA">Aceptadas ({countByState("ACEPTADA")})</option>
              <option value="RECHAZADA">Rechazadas ({countByState("RECHAZADA")})</option>
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
              : `No tienes solicitudes ${filter.toLowerCase()}s`}
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
            const clientName = req.cliente_nombre || "Cliente";
            const totalEstimado = Number(req.total_estimado ?? 0);

            return (
              <div key={req.id} className="client-request-item">
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
                        background: config.bg,
                        color: config.color,
                        border: `1px solid ${config.border}`,
                        fontWeight: 650,
                        fontSize: "11px",
                      }}
                    >
                      <StatusIcon size={14} />
                      {config.label}
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

                  {/* Total */}
                  <div style={{ margin: "12px 0 0 48px", display: "flex", alignItems: "center", gap: "6px" }}>
                    <span style={{ fontSize: "13px", color: "var(--muted)" }}>Total a cobrar:</span>
                    <strong style={{ fontSize: "17px", color: "var(--blue)", fontWeight: 800 }}>
                      L. {Number.isFinite(totalEstimado) ? totalEstimado.toFixed(2) : "0.00"}
                    </strong>
                  </div>
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
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
