import { useEffect, useState, useCallback, FormEvent } from "react";
import type { User } from "firebase/auth";
import { SearchX, Search } from "lucide-react";
import { api, errorMessage } from "./lib/api";
import { TechnicianCard, type CatalogTechnician } from "./components/TechnicianCard";

export default function CatalogPage({ user }: { user: User }) {
  const [technicians, setTechnicians] = useState<CatalogTechnician[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Estados para los filtros
  const [nombre, setNombre] = useState("");
  const [especialidad, setEspecialidad] = useState("");
  const [municipio, setMunicipio] = useState("");

  // Función para llamar al backend con los filtros aplicados
  const fetchCatalog = useCallback(
    (queryNombre = "", queryEspec = "", queryMuni = "") => {
      setLoading(true);

      // Construimos los parámetros de la URL inteligentemente
      const params = new URLSearchParams();
      if (queryNombre.trim()) params.append("nombre", queryNombre.trim());
      if (queryEspec.trim()) params.append("especialidad", queryEspec.trim());
      if (queryMuni.trim()) params.append("municipio", queryMuni.trim());

      const url = `/technicians/catalog${params.toString() ? `?${params.toString()}` : ""}`;

      api<CatalogTechnician[]>(url, user)
        .then((data) => {
          setTechnicians(data);
          setError("");
        })
        .catch((err) => setError(errorMessage(err)))
        .finally(() => setLoading(false));
    },
    [user],
  );

  // Carga inicial
  useEffect(() => {
    fetchCatalog();
  }, [fetchCatalog]);

  // Manejar el clic en "Aplicar filtros"
  const handleFilterSubmit = (e: FormEvent) => {
    e.preventDefault();
    fetchCatalog(nombre, especialidad, municipio);
  };

  // Manejar el clic en "Limpiar filtros"
  const handleClearFilters = () => {
    setNombre("");
    setEspecialidad("");
    setMunicipio("");
    fetchCatalog("", "", ""); // Llama al backend sin ningún filtro
  };

  return (
    <main className="account" style={{ margin: "0 auto 90px", maxWidth: "1200px" }}>
      <div style={{ marginBottom: "2.5rem" }}>
        <h1 style={{ marginTop: 0 }}>Encuentra al experto ideal</h1>
        <p>Explora nuestro catálogo de técnicos verificados listos para ayudarte.</p>
      </div>

      {error && (
        <div className="alert" role="alert" style={{ marginBottom: "2rem" }}>
          {error}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", gap: "2rem", alignItems: "start" }}>
        {/* PANEL DE FILTROS LATERAL */}
        <aside className="account-card" style={{ padding: "1.5rem", position: "sticky", top: "2rem" }}>
          <h2 style={{ fontSize: "1.2rem", marginTop: 0, marginBottom: "1.5rem", borderBottom: "1px solid #e5e7eb", paddingBottom: "0.5rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <Search size={20} color="#6b7280" /> Búsqueda y Filtros
          </h2>

          <form onSubmit={handleFilterSubmit} style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
            {/* Nuevo campo propuesto: Búsqueda por Nombre */}
            <div>
              <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: 500, fontSize: "0.9rem" }}>Nombre del profesional</label>
              <input type="text" placeholder="Ej. Juan Pérez" className="input" style={{ width: "100%" }} value={nombre} onChange={(e) => setNombre(e.target.value)} />
            </div>

            <div>
              <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: 500, fontSize: "0.9rem" }}>Especialidad</label>
              <input type="text" placeholder="Ej. Electricidad" className="input" style={{ width: "100%" }} value={especialidad} onChange={(e) => setEspecialidad(e.target.value)} />
            </div>

            <div>
              <label style={{ display: "block", marginBottom: "0.5rem", fontWeight: 500, fontSize: "0.9rem" }}>Municipio o Sector</label>
              <input type="text" placeholder="Ej. Tegucigalpa" className="input" style={{ width: "100%" }} value={municipio} onChange={(e) => setMunicipio(e.target.value)} />
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", marginTop: "1rem" }}>
              {/* Botón para limpiar (gris, más sutil) - Solo aparece si hay texto en algún filtro */}
              {(nombre || especialidad || municipio) && (
                <button
                  type="button"
                  onClick={handleClearFilters}
                  className="button button-outline"
                  style={{ width: "100%", padding: "0.5rem", fontSize: "0.9rem", color: "#6b7280", borderColor: "#d1d5db" }}
                  disabled={loading}
                >
                  Limpiar filtros
                </button>
              )}

              {/* Botón principal para aplicar (azul) */}
              <button type="submit" className="button" style={{ width: "100%" }} disabled={loading}>
                {loading ? "Buscando..." : "Aplicar filtros"}
              </button>
            </div>
          </form>
        </aside>

        {/* CATÁLOGO DE TARJETAS */}
        <div>
          {loading ? (
            <div style={{ display: "flex", justifyContent: "center", padding: "3rem", color: "#6b7280" }}>
              <p>Buscando técnicos...</p>
            </div>
          ) : technicians.length === 0 ? (
            <div style={{ textAlign: "center", padding: "4rem 2rem", background: "#f9fafb", borderRadius: "1rem", border: "2px dashed #e5e7eb" }}>
              <SearchX size={48} color="#9ca3af" style={{ margin: "0 auto 1rem" }} />
              <h2 style={{ fontSize: "1.25rem", margin: "0 0 0.5rem 0", color: "#111827" }}>No se encontraron resultados</h2>
              <p style={{ color: "#6b7280", margin: 0 }}>Intenta probar con otros términos de búsqueda.</p>
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "1.5rem" }}>
              {technicians.map((tech) => (
                <TechnicianCard key={tech.usuario_id} technician={tech} />
              ))}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
