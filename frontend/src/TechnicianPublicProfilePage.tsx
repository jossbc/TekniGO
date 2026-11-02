import { useEffect, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import type { User } from "firebase/auth";
import { ShieldCheck, Star, ArrowLeft, Briefcase, Wrench } from "lucide-react";
import { api, errorMessage } from "./lib/api";

type PublicService = {
  id: string;
  nombre: string;
  descripcion: string;
};

type TechnicianProfileData = {
  usuario_id: string;
  nombre_completo: string;
  especialidades: string[];
  servicios: PublicService[];
  estado_verificacion: string;
  tarifa_base: number;
  unidad_cobro: string;
  calificacion_promedio: number;
};

export default function TechnicianPublicProfilePage({ user }: { user: User }) {
  const { id } = useParams<{ id: string }>();
  const [technician, setTechnician] = useState<TechnicianProfileData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchProfile = useCallback(() => {
    setLoading(true);
    // ST-04: Conectando con el endpoint específico del perfil
    api<TechnicianProfileData>(`/technicians/profile/${id}`, user)
      .then((data) => {
        setTechnician(data);
        setError("");
      })
      .catch((err) => {
        setError(errorMessage(err));
      })
      .finally(() => {
        setLoading(false);
      });
  }, [id, user]);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  if (loading)
    return (
      <main className="account" style={{ margin: "0 auto 90px", padding: "3rem", textAlign: "center" }}>
        Cargando perfil...
      </main>
    );
  if (error || !technician)
    return (
      <main className="account" style={{ margin: "0 auto 90px", padding: "3rem", color: "red", textAlign: "center" }}>
        {error}
      </main>
    );

  return (
    <main className="account" style={{ margin: "0 auto 90px", maxWidth: "900px" }}>
      <Link to="/catalogo" style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem", color: "#6b7280", textDecoration: "none", marginBottom: "2rem", fontWeight: 500 }}>
        <ArrowLeft size={20} /> Volver al catálogo
      </Link>

      <div className="account-card" style={{ padding: "2.5rem" }}>
        {/* Encabezado Principal */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "2.5rem", borderBottom: "1px solid #f3f4f6", paddingBottom: "2rem" }}>
          <div>
            <h1 style={{ margin: "0 0 0.75rem 0", display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "2.2rem" }}>
              {technician.nombre_completo}
              {technician.estado_verificacion === "VERIFICADO" && <ShieldCheck size={32} color="#2563eb" aria-label="Verificado" />}
            </h1>
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              {technician.especialidades.map((esp, idx) => (
                <span key={idx} style={{ background: "#eff6ff", color: "#1d4ed8", padding: "0.35rem 1rem", borderRadius: "999px", fontSize: "0.95rem", fontWeight: 600 }}>
                  {esp}
                </span>
              ))}
            </div>
          </div>

          <div style={{ textAlign: "right", background: "#f9fafb", padding: "1.5rem", borderRadius: "1rem" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "0.25rem", color: "#eab308", fontWeight: 700, fontSize: "1.25rem", marginBottom: "0.5rem" }}>
              <Star size={24} fill="currentColor" />
              <span>{technician.calificacion_promedio.toFixed(1)}</span>
            </div>
            <p style={{ margin: "0", color: "#6b7280", fontSize: "0.9rem" }}>Tarifa base</p>
            <p style={{ margin: 0, fontWeight: 700, fontSize: "1.5rem", color: "#111827" }}>
              L. {technician.tarifa_base.toFixed(2)} <span style={{ fontSize: "1rem", color: "#6b7280", fontWeight: 400 }}>/ {technician.unidad_cobro.replace("_", " ").toLowerCase()}</span>
            </p>
          </div>
        </div>

        {/* Sección de Servicios Específicos */}
        <section style={{ marginBottom: "3rem" }}>
          <h2 style={{ fontSize: "1.5rem", marginBottom: "1.5rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <Wrench size={24} color="#4b5563" /> Servicios Ofrecidos
          </h2>
          {technician.servicios.length > 0 ? (
            <div style={{ display: "grid", gap: "1rem" }}>
              {technician.servicios.map((servicio) => (
                <div key={servicio.id} style={{ padding: "1.5rem", border: "1px solid #e5e7eb", borderRadius: "0.75rem", background: "#fff" }}>
                  <h3 style={{ margin: "0 0 0.5rem 0", fontSize: "1.1rem" }}>{servicio.nombre}</h3>
                  <p style={{ margin: 0, color: "#4b5563", lineHeight: 1.5 }}>{servicio.descripcion}</p>
                </div>
              ))}
            </div>
          ) : (
            <p style={{ color: "#6b7280", fontStyle: "italic" }}>Este técnico aún no ha detallado servicios específicos.</p>
          )}
        </section>

        {/* Sección de Trabajos (Galería) - HU-13 ST-05 */}
        <section style={{ borderTop: "1px solid #f3f4f6", paddingTop: "2.5rem" }}>
          <h2 style={{ fontSize: "1.5rem", marginBottom: "1.5rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <Briefcase size={24} color="#4b5563" /> Galería de Trabajos
          </h2>
          <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "150px", background: "#f9fafb", borderRadius: "0.75rem", color: "#9ca3af", border: "2px dashed #e5e7eb" }}>
            Aún no hay imágenes en el portafolio.
          </div>
        </section>

        {/* Botón de Contratación */}
        <div style={{ marginTop: "3rem", textAlign: "center" }}>
          <button className="button" style={{ padding: "1.2rem 4rem", fontSize: "1.2rem" }}>
            Solicitar Contratación
          </button>
        </div>
      </div>
    </main>
  );
}
