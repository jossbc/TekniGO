import { MapPin, Phone } from "lucide-react";

export interface ServiceLocationContactCardProps {
  municipio?: string | null;
  sector?: string | null;
  direccionExacta?: string | null;
  telefono?: string | null;
  isPendingAcceptance?: boolean;
}

/**
 * Patrón Componente Reutilizable (Composite UI) + Principios SOLID (SRP, OCP, ISP):
 * Encapsula la visualización de la ubicación del trabajo y el contacto telefónico del cliente
 * respetando las reglas de privacidad antes y después de aceptar la contratación.
 */
export function ServiceLocationContactCard({
  municipio,
  sector,
  direccionExacta,
  telefono,
  isPendingAcceptance = false,
}: ServiceLocationContactCardProps) {
  const generalLocation = [municipio, sector].filter(Boolean).join(", ");
  const fullAddress = [municipio, sector, direccionExacta].filter(Boolean).join(", ");

  return (
    <div
      style={{
        margin: "12px 0 0 48px",
        padding: "12px 14px",
        background: "#f8fafc",
        border: "1px solid #e2e8f0",
        borderRadius: "10px",
        fontSize: "13px",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: "8px" }}>
        <MapPin size={16} color="var(--blue)" style={{ flexShrink: 0, marginTop: "2px" }} />
        <div>
          <strong style={{ color: "var(--navy)" }}>Ubicación del trabajo:</strong>{" "}
          {direccionExacta ? (
            <span style={{ color: "#334155" }}>{fullAddress}</span>
          ) : (
            <span style={{ color: "#334155" }}>
              {generalLocation || "Ubicación general"}{" "}
              {isPendingAcceptance && (
                <em style={{ color: "#64748b", fontSize: "12px", display: "block", marginTop: "2px" }}>
                  (La dirección exacta y el teléfono se revelarán al aceptar la solicitud)
                </em>
              )}
            </span>
          )}
        </div>
      </div>

      {telefono && (
        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "8px" }}>
          <Phone size={15} color="#16a34a" style={{ flexShrink: 0 }} />
          <span>
            <strong style={{ color: "var(--navy)" }}>Teléfono del cliente:</strong>{" "}
            <a
              href={`tel:${telefono}`}
              style={{ color: "#15803d", fontWeight: 700, textDecoration: "underline" }}
            >
              {telefono}
            </a>
          </span>
        </div>
      )}
    </div>
  );
}

export default ServiceLocationContactCard;