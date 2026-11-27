import { Star } from "lucide-react";
import type { ReputationSummary } from "../lib/reviews";

type RoleReputationCardProps = {
  title: string;
  sourcePlural: string;
  sourceSingular: string;
  summary: ReputationSummary | null;
};

function RoleReputationIndicator({
  title,
  sourcePlural,
  sourceSingular,
  summary,
}: RoleReputationCardProps) {
  const total = summary?.total_resenas ?? 0;
  const hasScore = Boolean(summary && !summary.sin_calificacion && summary.promedio != null && total > 0);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.45rem",
        padding: "1rem 1.25rem",
        background: "#f8fafc",
        borderRadius: 12,
        border: "1px solid var(--line)",
      }}
    >
      <span
        style={{
          fontSize: "0.82rem",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          color: "#475569",
        }}
      >
        {title}
      </span>

      {hasScore && summary?.promedio != null ? (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span style={{ display: "inline-flex", alignItems: "center", color: "#eab308" }}>
              <Star size={22} fill="currentColor" />
            </span>
            <span style={{ fontSize: "1.55rem", fontWeight: 800, color: "var(--ink)" }}>
              {summary.promedio.toFixed(1)}
            </span>
            <span style={{ color: "#6b7280", fontSize: "0.95rem" }}>/ 5.0</span>
          </div>
          <span style={{ fontSize: "0.9rem", color: "#374151", fontWeight: 500 }}>
            Basado en {total}{" "}
            {total === 1
              ? `reseña de servicio completado (de ${sourceSingular})`
              : `reseñas de servicios completados (de ${sourcePlural})`}
          </span>
        </>
      ) : (
        <>
          <span style={{ fontSize: "1.05rem", fontWeight: 700, color: "#4b5563" }}>
            Sin calificaciones aún
          </span>
          <span style={{ fontSize: "0.88rem", color: "#6b7280" }}>
            0 reseñas recibidas de {sourcePlural}
          </span>
        </>
      )}
    </div>
  );
}

export function RoleReputationSummary({
  isClient,
  isTechnician,
  clientReputation,
  techReputation,
  fallbackSummary,
}: {
  isClient: boolean;
  isTechnician: boolean;
  clientReputation: ReputationSummary | null;
  techReputation: ReputationSummary | null;
  fallbackSummary?: ReputationSummary | null;
}) {
  if (!isClient && !isTechnician) {
    if (!fallbackSummary || fallbackSummary.sin_calificacion || fallbackSummary.promedio == null) {
      return null;
    }
    const total = fallbackSummary.total_resenas;
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "1rem",
          flexWrap: "wrap",
          padding: "1rem 1.25rem",
          background: "#f8fafc",
          borderRadius: 12,
          marginBottom: "1.25rem",
          border: "1px solid var(--line)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span style={{ display: "inline-flex", alignItems: "center", color: "#eab308" }}>
            <Star size={24} fill="currentColor" />
          </span>
          <span style={{ fontSize: "1.6rem", fontWeight: 800, color: "var(--ink)" }}>
            {fallbackSummary.promedio.toFixed(1)}
          </span>
          <span style={{ color: "#6b7280", fontSize: "0.95rem" }}>/ 5.0</span>
        </div>
        <div style={{ height: "24px", width: "1px", background: "#cbd5e1" }} />
        <span style={{ fontSize: "0.95rem", color: "#374151", fontWeight: 500 }}>
          Basado en {total} {total === 1 ? "reseña de servicio completado" : "reseñas de servicios completados"}
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: isClient && isTechnician ? "repeat(auto-fit, minmax(240px, 1fr))" : "1fr",
        gap: "1rem",
        marginBottom: "1.25rem",
      }}
    >
      {isClient && (
        <RoleReputationIndicator
          title="Como Cliente"
          sourceSingular="técnico"
          sourcePlural="técnicos"
          summary={clientReputation}
        />
      )}
      {isTechnician && (
        <RoleReputationIndicator
          title="Como Técnico"
          sourceSingular="cliente"
          sourcePlural="clientes"
          summary={techReputation}
        />
      )}
    </div>
  );
}

export default RoleReputationSummary;
