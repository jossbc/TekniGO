import { useEffect, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import type { User } from "firebase/auth";
import { ShieldCheck, Star, ArrowLeft, Briefcase, Wrench, MapPin } from "lucide-react";
import { api, errorMessage } from "./lib/api";
import type { CatalogTechnician } from "./components/TechnicianCard";
import type { CoverageZone } from "./CoverageZoneSelector";
import LoadingState from "./components/LoadingState";

type PublicService = {
  id: string;
  nombre: string;
  descripcion: string;
};

type TechnicianProfileData = CatalogTechnician & {
  servicios: PublicService[];
  zonas_cobertura?: CoverageZone[];
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
    return <LoadingState variant="screen" message="Cargando perfil del técnico." />;
  if (error || !technician)
    return (
      <main className="account" style={{ margin: "0 auto 90px", padding: "3rem", color: "red", textAlign: "center" }}>
        {error}
      </main>
    );

  const coverageZones = technician.zonas_cobertura;

  return (
    <main className="account" style={{ margin: "0 auto 90px", maxWidth: "900px" }}>
      <Link to="/catalogo" className="public-profile-back">
        <ArrowLeft size={20} /> Volver al catálogo
      </Link>

      <div className="account-card" style={{ padding: "2.5rem" }}>
        {/* Encabezado Principal */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "2.5rem", borderBottom: "1px solid #f3f4f6", paddingBottom: "2rem" }}>
          <div>
            <h1 style={{ margin: "0 0 0.75rem 0", display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "2.2rem" }}>
              {technician.nombre_completo}
              {technician.tecnico_verificado && <ShieldCheck size={32} color="#2563eb" aria-label="Verificado" />}
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
              <span>{technician.calificacion_promedio == null ? "Sin calificaciones" : technician.calificacion_promedio.toFixed(1)}</span>
            </div>
            <p style={{ margin: "0", color: "#6b7280", fontSize: "0.9rem" }}>Tarifas según el servicio</p>
          </div>
        </div>

        <section className="public-profile-coverage" aria-labelledby="public-profile-coverage-title">
          <h2 id="public-profile-coverage-title"><MapPin size={24} aria-hidden="true" /> Zonas de cobertura</h2>
          {coverageZones?.length ? (
            <ul>
              {coverageZones.map(({ departamento, municipio, sector }) => (
                <li key={JSON.stringify([departamento, municipio, sector])}>
                  <strong>{municipio}, {departamento}</strong>
                  <span>{sector}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p>No hay zonas de cobertura publicadas.</p>
          )}
        </section>

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
