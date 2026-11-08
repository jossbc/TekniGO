import { ShieldCheck, Star } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "./ui";

export type CatalogTechnician = {
  usuario_id: string;
  nombre_completo: string;
  especialidades: string[];
  estado_verificacion: string;
  tecnico_verificado: boolean;
  estado_certificacion: string;
  calificacion_promedio: number | null;
};

export function TechnicianCard({ technician }: { technician: CatalogTechnician }) {
  const isVerified = technician.tecnico_verificado;

  return (
    <div className="account-card" style={{ display: "flex", flexDirection: "column", gap: "1rem", padding: "1.5rem", height: "100%" }}>
      {/* Encabezado: Nombre, Verificación y Calificación */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h3 style={{ margin: "0 0 0.5rem 0", display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "1.25rem" }}>
            {technician.nombre_completo}
            {isVerified && <ShieldCheck size={18} color="#2563eb" aria-label="Técnico Verificado" />}
          </h3>
          <div style={{ display: "flex", alignItems: "center", gap: "0.25rem", color: "#eab308", fontWeight: 600 }}>
            <Star size={16} fill="currentColor" />
            <span>{technician.calificacion_promedio == null ? "Sin calificaciones" : technician.calificacion_promedio.toFixed(1)}</span>
          </div>
        </div>
      </div>

      {/* Etiquetas de Especialidades */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
        {technician.especialidades.length > 0 ? (
          technician.especialidades.map((esp, idx) => (
            <span key={idx} style={{ background: "#f3f4f6", color: "#374151", padding: "0.25rem 0.75rem", borderRadius: "999px", fontSize: "0.85rem", fontWeight: 500 }}>
              {esp}
            </span>
          ))
        ) : (
          <span style={{ color: "#6b7280", fontSize: "0.85rem" }}>Sin especialidad registrada</span>
        )}
      </div>

      {/* Pie de tarjeta: Tarifa y Botón */}
      <div style={{ marginTop: "auto", paddingTop: "1.5rem", display: "flex", justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <p style={{ margin: 0, fontSize: "0.85rem", color: "#6b7280" }}>Tarifas según el servicio</p>
        </div>

        <Link to={`/tecnicos/${technician.usuario_id}`} style={{ textDecoration: "none" }}>
          <Button type="button" className="button-outline" style={{ padding: "0.6rem 1.2rem", width: "auto" }}>
            Ver perfil
          </Button>
        </Link>
      </div>
    </div>
  );
}
