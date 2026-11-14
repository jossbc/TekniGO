import { useState, useEffect } from "react";
import type { User } from "firebase/auth";
import { Link } from "react-router-dom";
import { ArrowUpRight, Briefcase, CheckCircle2, CircleAlert, Clock3, Inbox, Check, MapPin, X } from "lucide-react";
import { errorMessage } from "../lib/api";
import {
  acceptTechnicianDirectRequest,
  listTechnicianDirectRequests,
  rejectTechnicianDirectRequest,
  type TechnicianDirectRequest,
} from "../lib/directRequests";
import {
  listAvailableOpportunitiesApi,
  type AvailableOpportunitiesResponse,
} from "../lib/openRequests";
import LoadingState from "./LoadingState";
import { Button } from "./ui";

export type DirectRequest = TechnicianDirectRequest;

function normalizeEstado(estado?: string): string {
  return (estado || "PENDIENTE").trim().toUpperCase();
}

function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return "Por confirmar";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("es-HN", {
    day: "numeric",
    month: "short",
  });
}

export default function TechnicianIncomingRequestsCard({ user }: { user: User }) {
  const [requests, setRequests] = useState<DirectRequest[]>([]);
  const [openOpportunities, setOpenOpportunities] = useState<AvailableOpportunitiesResponse | null>(null);
  const [openError, setOpenError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState("");
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError("");
    setOpenError("");

    Promise.all([
      listTechnicianDirectRequests(user).catch((err) => {
        if (active) setLoadError(errorMessage(err));
        return [] as DirectRequest[];
      }),
      listAvailableOpportunitiesApi(user, { limit: 3 }).catch((err) => {
        if (active) setOpenError(errorMessage(err));
        return null;
      }),
    ])
      .then(([directData, openData]) => {
        if (!active) return;
        setRequests(Array.isArray(directData) ? directData : []);
        setOpenOpportunities(openData);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [user, refreshTick]);

  const pendingRequests = requests.filter((r) => normalizeEstado(r.estado) === "PENDIENTE");

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

  function handleAccept(id: string) {
    return handleDecision(id, "accept");
  }

  function handleReject(id: string) {
    return handleDecision(id, "reject");
  }

  if (loading) {
    return (
      <section className="account-card">
        <LoadingState message="Cargando solicitudes entrantes." />
      </section>
    );
  }

  return (
    <section className="account-card" aria-labelledby="incoming-requests-heading">
      <span className="step-icon">
        <Inbox size={22} />
      </span>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.75rem", flexWrap: "wrap" }}>
        <h2 id="incoming-requests-heading" style={{ margin: 0 }}>
          Solicitudes entrantes
        </h2>
        {pendingRequests.length > 0 && (
          <span
            className="status-pill"
            style={{ background: "#fff7ed", color: "#9a3412", fontWeight: 700 }}
          >
            <Clock3 size={15} />
            {pendingRequests.length} pendiente{pendingRequests.length !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      <p>
        {loadError
          ? "No pudimos consultar tus solicitudes directas en este momento."
          : pendingRequests.length === 0
            ? "No tienes solicitudes directas pendientes en este momento."
            : "Clientes te han enviado solicitudes directas. Revisa y decide si aceptar o rechazar."}
      </p>

      {actionNotice && (
        <div className="success-alert" role="status" style={{ margin: "8px 0 12px", padding: "10px 12px", fontSize: "12.5px" }}>
          <CheckCircle2 size={16} />
          <span>{actionNotice}</span>
        </div>
      )}

      {actionError && (
        <div className="alert" role="alert" style={{ margin: "8px 0 12px" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
            <CircleAlert size={15} />
            {actionError}
          </span>
        </div>
      )}

      {loadError ? (
        <div className="alert" role="alert" style={{ marginTop: "8px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px", flexWrap: "wrap" }}>
            <span>{loadError}</span>
            <Button
              type="button"
              className="button-outline"
              style={{ width: "auto", minHeight: "34px", padding: "6px 12px", fontSize: "12px" }}
              onClick={() => setRefreshTick((tick) => tick + 1)}
            >
              Reintentar
            </Button>
          </div>
        </div>
      ) : pendingRequests.length === 0 ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "10px",
            padding: "14px",
            borderRadius: "12px",
            border: "1px dashed var(--line)",
            background: "var(--surface)",
            color: "var(--muted)",
            fontSize: "12.5px",
            marginTop: "4px",
          }}
        >
          <Inbox size={18} style={{ color: "var(--blue)", flexShrink: 0 }} />
          <span>Cuando recibas una nueva petición directa de un cliente, aparecerá aquí para responder al instante.</span>
        </div>
      ) : (
        <div style={{ display: "grid", gap: "10px", marginTop: "4px" }}>
          {pendingRequests.slice(0, 3).map((req) => {
            const clientName = req.cliente_nombre || "Cliente";
            const totalEstimado = Number(req.total_estimado ?? 0);
            return (
              <div
                key={req.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "12px",
                  padding: "12px 14px",
                  border: "1px solid var(--line)",
                  borderRadius: "12px",
                  background: "#fff",
                  flexWrap: "wrap",
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        justifyContent: "center",
                        width: "28px",
                        height: "28px",
                        borderRadius: "50%",
                        background: "#eff6ff",
                        color: "var(--blue)",
                        fontSize: "11px",
                        fontWeight: 750,
                        flexShrink: 0,
                      }}
                    >
                      {clientName.charAt(0).toUpperCase()}
                    </span>
                    <strong style={{ fontSize: "13px", color: "var(--ink)" }}>
                      {clientName}
                    </strong>
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--muted)", marginLeft: "36px" }}>
                    {req.servicio_nombre}
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--muted)", marginLeft: "36px", marginTop: "2px" }}>
                    <strong style={{ color: "var(--blue)", fontWeight: 700 }}>
                      L. {Number.isFinite(totalEstimado) ? totalEstimado.toFixed(2) : "0.00"}
                    </strong>
                    {" · "}
                    {formatShortDate(req.fecha_hora)}
                  </div>
                </div>

                {/* Botones de acción */}
                <div style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
                  <button
                    type="button"
                    onClick={() => handleAccept(req.id)}
                    disabled={actionLoading === req.id}
                    title="Aceptar solicitud"
                    aria-label={`Aceptar solicitud de ${clientName}`}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      width: "36px",
                      height: "36px",
                      border: "1px solid #bbf7d0",
                      borderRadius: "10px",
                      background: "#f0fdf4",
                      color: "#166534",
                      cursor: actionLoading === req.id ? "wait" : "pointer",
                      opacity: actionLoading === req.id ? 0.6 : 1,
                    }}
                  >
                    <Check size={16} strokeWidth={2.5} />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleReject(req.id)}
                    disabled={actionLoading === req.id}
                    title="Rechazar solicitud"
                    aria-label={`Rechazar solicitud de ${clientName}`}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      width: "36px",
                      height: "36px",
                      border: "1px solid #fecaca",
                      borderRadius: "10px",
                      background: "#fef2f2",
                      color: "#991b1b",
                      cursor: actionLoading === req.id ? "wait" : "pointer",
                      opacity: actionLoading === req.id ? 0.6 : 1,
                    }}
                  >
                    <X size={16} strokeWidth={2.5} />
                  </button>
                </div>
              </div>
            );
          })}
          {pendingRequests.length > 3 && (
            <p style={{ margin: 0, fontSize: "12px", color: "var(--muted)", textAlign: "center" }}>
              +{pendingRequests.length - 3} solicitud{pendingRequests.length - 3 !== 1 ? "es" : ""} más
            </p>
          )}
        </div>
      )}

      <div style={{ marginTop: "12px" }}>
        <Link to="/tecnico/solicitudes">
          Ver todas las solicitudes directas <ArrowUpRight size={15} />
        </Link>
      </div>

      {/* Bloque de Solicitudes Abiertas (Licitaciones) en tu zona de cobertura */}
      <div
        style={{
          marginTop: "18px",
          paddingTop: "16px",
          borderTop: "1px solid var(--line)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "0.75rem",
            flexWrap: "wrap",
            marginBottom: "8px",
          }}
        >
          <h3 style={{ margin: 0, fontSize: "14px", display: "inline-flex", alignItems: "center", gap: "6px" }}>
            <Briefcase size={16} style={{ color: "var(--blue)" }} />
            Solicitudes abiertas en tu zona (licitaciones)
          </h3>
          {openOpportunities && openOpportunities.total > 0 && (
            <span
              className="status-pill"
              style={{ background: "#eff6ff", color: "#1d4ed8", fontWeight: 700 }}
            >
              {openOpportunities.total} disponible{openOpportunities.total !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        {openError ? (
          <div
            style={{
              padding: "10px 12px",
              borderRadius: "10px",
              border: "1px solid #fed7aa",
              background: "#fff7ed",
              color: "#9a3412",
              fontSize: "12.5px",
            }}
          >
            <div>{openError}</div>
            <Link to="/perfil" style={{ display: "inline-flex", alignItems: "center", gap: "4px", marginTop: "6px", fontWeight: 700 }}>
              Configurar disponibilidad o perfil <ArrowUpRight size={14} />
            </Link>
          </div>
        ) : openOpportunities && openOpportunities.solicitudes.length > 0 ? (
          <div style={{ display: "grid", gap: "8px" }}>
            {openOpportunities.solicitudes.slice(0, 3).map((item) => (
              <div
                key={item.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "12px",
                  padding: "10px 12px",
                  border: "1px solid #bfdbfe",
                  borderRadius: "10px",
                  background: "#f8fafc",
                  flexWrap: "wrap",
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "13px", fontWeight: 700, color: "var(--ink)" }}>
                    {item.especialidad?.nombre || "Solicitud abierta"}
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--muted)", marginTop: "2px", display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: "3px" }}>
                      <MapPin size={13} /> {item.municipio}, {item.sector}
                    </span>
                    {" · "}
                    <strong style={{ color: "var(--blue)" }}>
                      L. {Number(item.presupuesto ?? 0).toFixed(2)}
                    </strong>
                  </div>
                </div>
                <Link
                  className="button button-outline"
                  to="/tecnico/oportunidades"
                  style={{ width: "auto", minHeight: "32px", padding: "4px 10px", fontSize: "12px" }}
                >
                  Ver y postular
                </Link>
              </div>
            ))}
          </div>
        ) : (
          <div
            style={{
              padding: "10px 12px",
              borderRadius: "10px",
              border: "1px dashed var(--line)",
              background: "var(--surface)",
              color: "var(--muted)",
              fontSize: "12.5px",
            }}
          >
            <span>
              {openOpportunities?.mensaje ||
                "No hay solicitudes abiertas en tus zonas de cobertura y especialidades en este momento."}
            </span>
            {openOpportunities?.diagnostico_cobertura &&
              (!openOpportunities.diagnostico_cobertura.tiene_especialidades ||
                !openOpportunities.diagnostico_cobertura.tiene_zonas_cobertura) && (
                <div style={{ marginTop: "6px" }}>
                  <Link to="/perfil" style={{ fontWeight: 700 }}>
                    Ir a mi perfil para agregar especialidades y zonas de cobertura →
                  </Link>
                </div>
              )}
          </div>
        )}

        <div style={{ marginTop: "10px" }}>
          <Link to="/tecnico/oportunidades">
            Ir al tablero de ofertas abiertas <ArrowUpRight size={15} />
          </Link>
        </div>
      </div>
    </section>
  );
}
