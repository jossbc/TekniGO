import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import { Link } from "react-router-dom";
import { ArrowUpRight, Briefcase, CheckCircle2, Clock3 } from "lucide-react";
import { errorMessage } from "../lib/api";
import {
  listTechnicianDirectRequests,
  type TechnicianDirectRequest,
} from "../lib/directRequests";
import LoadingState from "./LoadingState";

function normalizeEstado(estado?: string): string {
  return (estado || "").trim().toUpperCase();
}

const ACTIVE_STATES = new Set(["ACEPTADA", "ASIGNADA", "EN_PROCESO", "EN PROCESO"]);

export default function TechnicianActiveJobsCard({ user }: { user: User }) {
  const [jobs, setJobs] = useState<TechnicianDirectRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError("");

    listTechnicianDirectRequests(user)
      .then((data) => {
        if (!active) return;
        const list = Array.isArray(data) ? data : [];
        setJobs(list.filter((item) => ACTIVE_STATES.has(normalizeEstado(item.estado))));
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
  }, [user]);

  if (loading) {
    return (
      <section className="account-card">
        <LoadingState message="Cargando trabajos activos." />
      </section>
    );
  }

  return (
    <section className="account-card" aria-labelledby="active-jobs-heading">
      <span className="step-icon">
        <Briefcase size={22} />
      </span>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.75rem", flexWrap: "wrap" }}>
        <h2 id="active-jobs-heading" style={{ margin: 0 }}>
          Mis trabajos
        </h2>
        {jobs.length > 0 && (
          <span
            className="status-pill"
            style={{ background: "#eff6ff", color: "#1d4ed8", fontWeight: 700 }}
          >
            <Clock3 size={15} />
            {jobs.length} en curso
          </span>
        )}
      </div>

      <p>
        {loadError
          ? "No pudimos consultar tus trabajos en este momento."
          : jobs.length === 0
            ? "Aquí verás tus trabajos por iniciar o en proceso, y podrás acceder a todo tu historial (incluyendo completados)."
            : "Tienes trabajos asignados listos para iniciar o reportar como finalizados."}
      </p>

      {loadError ? (
        <div className="alert" role="alert" style={{ marginTop: "8px" }}>
          <span>{loadError}</span>
        </div>
      ) : jobs.length === 0 ? (
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
          <CheckCircle2 size={18} style={{ color: "var(--blue)", flexShrink: 0 }} />
          <span>Cuando un cliente acepte tu propuesta o aceptes una solicitud directa, aparecerá aquí.</span>
        </div>
      ) : (
        <div style={{ display: "grid", gap: "10px", marginTop: "4px" }}>
          {jobs.slice(0, 3).map((job) => {
            const state = normalizeEstado(job.estado);
            const stateLabel =
              state === "ACEPTADA" || state === "ASIGNADA"
                ? "Aceptada · Por iniciar"
                : job.pendiente_recepcion
                  ? "En proceso · Pendiente de recepción"
                  : "En proceso · Por finalizar";
            return (
              <div
                key={job.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "12px",
                  padding: "12px 14px",
                  border: "1px solid var(--line)",
                  borderRadius: "12px",
                  background: "var(--surface)",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 700, fontSize: "13.5px", color: "var(--ink)" }}>
                    {job.servicio_nombre || "Servicio"}
                  </div>
                  <div style={{ fontSize: "12.5px", color: "var(--muted)", marginTop: "2px" }}>
                    {job.cliente_nombre || "Cliente"} · {stateLabel}
                  </div>
                </div>
                <Link
                  to="/tecnico/solicitudes"
                  className="button button-outline"
                  style={{ width: "auto", minHeight: "34px", padding: "6px 12px", fontSize: "12px", flexShrink: 0 }}
                >
                  Gestionar
                </Link>
              </div>
            );
          })}
        </div>
      )}

      <div style={{ marginTop: "12px" }}>
        <Link to="/tecnico/solicitudes">
          Ver todos mis trabajos y solicitudes <ArrowUpRight size={15} />
        </Link>
      </div>
    </section>
  );
}
