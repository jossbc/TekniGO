import { useEffect, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import type { User } from "firebase/auth";
import { Award, ShieldCheck, Star, ArrowLeft, Briefcase, Wrench, MapPin, MessageSquare, ThumbsUp } from "lucide-react";
import { api, errorMessage } from "./lib/api";
import type { CatalogTechnician } from "./components/TechnicianCard";
import type { CoverageZone } from "./CoverageZoneSelector";
import { formatReviewDate, type ServiceReview } from "./lib/reviews";
import LoadingState from "./components/LoadingState";
import { DirectRequestForm } from "./components/DirectRequestForm";

type PublicService = {
  id: string;
  nombre: string;
  descripcion: string;
};

type TechnicianProfileData = CatalogTechnician & {
  servicios: PublicService[];
  zonas_cobertura?: CoverageZone[];
  total_resenas?: number;
  resenas?: ServiceReview[];
};

export default function TechnicianPublicProfilePage({ user }: { user: User }) {
  const { id } = useParams<{ id: string }>();
  const [technician, setTechnician] = useState<TechnicianProfileData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Estados para controlar la visibilidad del formulario (HU-20)
  const [showForm, setShowForm] = useState(false);
  const [selectedServiceId, setSelectedServiceId] = useState<string>("");

  const fetchProfile = useCallback(() => {
    setLoading(true);
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

  if (loading) return <LoadingState variant="screen" message="Cargando perfil del técnico." />;
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

      <div className="account-card" style={{ padding: "2.5rem", position: "relative" }}>
        {/* Encabezado Principal */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "2.5rem", borderBottom: "1px solid #f3f4f6", paddingBottom: "2rem" }}>
          <div>
            <h1 style={{ margin: "0 0 0.75rem 0", display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "2.2rem" }}>
              {technician.nombre_completo}
              {technician.tecnico_verificado && <ShieldCheck size={32} color="#2563eb" aria-label="Verificado" />}
            </h1>
            {technician.tecnico_verificado && <p className="status-pill status-pill-verified">Técnico Verificado</p>}
            {technician.tecnico_verificado && technician.estado_certificacion === "CERTIFICADO" &&
              <p className="status-pill"><Award size={15} aria-hidden="true" /> Técnico Certificado</p>}
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              {technician.especialidades.map((esp, idx) => (
                <span key={idx} style={{ background: "#eff6ff", color: "#1d4ed8", padding: "0.35rem 1rem", borderRadius: "999px", fontSize: "0.95rem", fontWeight: 600 }}>
                  {esp}
                </span>
              ))}
            </div>
          </div>

          <div style={{ textAlign: "right", background: "#f9fafb", padding: "1.5rem", borderRadius: "1rem" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "0.35rem", color: "#eab308", fontWeight: 700, fontSize: "1.25rem", marginBottom: "0.5rem" }}>
              <Star size={24} fill="currentColor" />
              {technician.calificacion_promedio != null ? (
                <span>
                  {technician.calificacion_promedio.toFixed(1)}
                  <span style={{ fontSize: "0.9rem", color: "#6b7280", fontWeight: 500, marginLeft: "6px" }}>
                    ({technician.total_resenas || 0} {(technician.total_resenas || 0) === 1 ? "reseña" : "reseñas"})
                  </span>
                </span>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" }}>
                  <span style={{ fontSize: "1.1rem", color: "#4b5563" }}>Sin calificaciones</span>
                  <span
                    style={{
                      background: "#eff6ff",
                      color: "var(--blue)",
                      fontSize: "0.78rem",
                      padding: "2px 8px",
                      borderRadius: "999px",
                      fontWeight: 600,
                    }}
                  >
                    Nuevo en la aplicación
                  </span>
                </div>
              )}
            </div>
            <p style={{ margin: "0", color: "#6b7280", fontSize: "0.9rem" }}>Tarifas según el servicio</p>
          </div>
        </div>

        <section className="public-profile-coverage" aria-labelledby="public-profile-coverage-title">
          <h2 id="public-profile-coverage-title">
            <MapPin size={24} aria-hidden="true" /> Zonas de cobertura
          </h2>
          {coverageZones?.length ? (
            <ul>
              {coverageZones.map(({ departamento, municipio, sector }) => (
                <li key={JSON.stringify([departamento, municipio, sector])}>
                  <strong>
                    {municipio}, {departamento}
                  </strong>
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
                <div key={servicio.id} style={{ padding: "1.5rem", border: "1px solid #e5e7eb", borderRadius: "0.75rem", background: "#fff", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <h3 style={{ margin: "0 0 0.5rem 0", fontSize: "1.1rem" }}>{servicio.nombre}</h3>
                    <p style={{ margin: 0, color: "#4b5563", lineHeight: 1.5 }}>{servicio.descripcion}</p>
                  </div>
                  {/* Botón Solicitar Servicio (HU-20) */}
                  <button
                    onClick={() => {
                      setSelectedServiceId(servicio.id);
                      setShowForm(true);
                    }}
                    className="button"
                    style={{ padding: "0.5rem 1.5rem", fontSize: "0.95rem", marginLeft: "1rem" }}
                  >
                    Solicitar
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p style={{ color: "#6b7280", fontStyle: "italic" }}>Este técnico aún no ha detallado servicios específicos.</p>
          )}
        </section>

        {/* Sección de Reseñas y Calificaciones (EP-08: HU-46) */}
        <section style={{ borderTop: "1px solid #f3f4f6", paddingTop: "2.5rem", marginBottom: "3rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "1rem", marginBottom: "1.5rem" }}>
            <h2 style={{ fontSize: "1.5rem", margin: 0, display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <MessageSquare size={24} color="#4b5563" /> Reseñas y calificaciones
            </h2>
            {technician.total_resenas ? (
              <span style={{ fontWeight: 600, color: "#4b5563" }}>
                {technician.total_resenas} {technician.total_resenas === 1 ? "evaluación verificada" : "evaluaciones verificadas"}
              </span>
            ) : null}
          </div>

          {technician.resenas && technician.resenas.length > 0 ? (
            <div style={{ display: "grid", gap: "1.25rem" }}>
              {technician.resenas.map((resena) => (
                <article
                  key={resena.id}
                  style={{
                    padding: "1.5rem",
                    border: "1px solid #e5e7eb",
                    borderRadius: "0.85rem",
                    background: "#ffffff",
                    boxShadow: "0 1px 3px 0 rgba(0, 0, 0, 0.05)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem", flexWrap: "wrap", gap: "8px" }}>
                    <div>
                      <strong style={{ fontSize: "1rem", color: "var(--ink)" }}>{resena.evaluador_nombre}</strong>
                      <span style={{ marginLeft: "8px", fontSize: "0.85rem", color: "#6b7280" }}>
                        · {formatReviewDate(resena.publicada_en || resena.fecha)}
                      </span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "3px", color: "#eab308" }}>
                      {[...Array(5)].map((_, i) => (
                        <Star
                          key={i}
                          size={16}
                          fill={i < resena.puntuacion ? "currentColor" : "none"}
                          strokeWidth={i < resena.puntuacion ? 0 : 1.5}
                          stroke="#d1d5db"
                        />
                      ))}
                    </div>
                  </div>

                  {resena.aspectos_destacados && resena.aspectos_destacados.length > 0 && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "0.75rem" }}>
                      {resena.aspectos_destacados.map((aspecto, idx) => (
                        <span
                          key={idx}
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                            background: "#f0fdf4",
                            color: "#166534",
                            border: "1px solid #bbf7d0",
                            padding: "0.2rem 0.65rem",
                            borderRadius: "999px",
                            fontSize: "0.8rem",
                            fontWeight: 600,
                          }}
                        >
                          <ThumbsUp size={11} /> {aspecto}
                        </span>
                      ))}
                    </div>
                  )}

                  <p style={{ margin: 0, color: "#374151", lineHeight: 1.6, fontSize: "0.95rem" }}>
                    "{resena.comentario}"
                  </p>
                </article>
              ))}
            </div>
          ) : (
            <div
              style={{
                padding: "2rem",
                background: "#f9fafb",
                borderRadius: "0.75rem",
                textAlign: "center",
                border: "1px dashed #d1d5db",
              }}
            >
              <p style={{ margin: 0, color: "#6b7280", fontSize: "0.95rem", lineHeight: 1.6 }}>
                Este profesional es nuevo en la plataforma y aún no cuenta con reseñas publicadas. Una vez que complete servicios y los clientes compartan su experiencia, sus calificaciones aparecerán aquí.
              </p>
            </div>
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

        {showForm && id && (
          <DirectRequestForm
            tecnicoId={technician.usuario_id}
            coverageZones={coverageZones ?? []}
            servicioId={selectedServiceId}
            onSuccess={() => {
              setShowForm(false);
              alert("¡Solicitud enviada exitosamente al técnico!");
            }}
            onCancel={() => setShowForm(false)}
          />
        )}
      </div>
    </main>
  );
}
