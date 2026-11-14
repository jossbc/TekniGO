import { useEffect, useState, useCallback, FormEvent } from "react";
import type { User } from "firebase/auth";
import { SearchX, Search, SlidersHorizontal, Wrench, MapPin, RotateCcw, ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "./lib/api";
import { TechnicianCard, type CatalogTechnician } from "./components/TechnicianCard";
import hondurasLocation from "./data/hondurasLocation.json";
import { getMunicipiosByDepartamento, getSectorsForMunicipio } from "./data/hondurasSectors";

const departments = Object.keys(hondurasLocation).sort((a, b) => a.localeCompare(b, "es"));

export default function CatalogPage({ user }: { user: User }) {
  const [technicians, setTechnicians] = useState<CatalogTechnician[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [hasAppliedFilters, setHasAppliedFilters] = useState(false);

  // Estados para los filtros
  const [especialidad, setEspecialidad] = useState("");
  const [municipio, setMunicipio] = useState("");
  const [departamento, setDepartamento] = useState("");
  const [sector, setSector] = useState("");

  // Función para llamar al backend con los filtros aplicados
  const fetchCatalog = useCallback(
    (queryEspec = "", queryMuni = "", queryDepto = "", querySector = "") => {
      setLoading(true);
      setError("");

      // Construimos los parámetros de la URL inteligentemente
      const params = new URLSearchParams();
      if (queryEspec.trim()) params.append("especialidad", queryEspec.trim());
      if (queryMuni.trim()) params.append("municipio", queryMuni.trim());
      if (queryDepto) params.append("departamento", queryDepto);
      if (querySector.trim()) params.append("sector", querySector.trim());

      const query = params.toString();
      setHasAppliedFilters(Boolean(query));
      const url = `/technicians/catalog${query ? `?${query}` : ""}`;

      api<CatalogTechnician[]>(url, user)
        .then((data) => {
          setTechnicians(data);
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
    fetchCatalog(especialidad, municipio, departamento, sector);
  };

  // Manejar el clic en "Limpiar filtros"
  const handleClearFilters = () => {
    setEspecialidad("");
    setMunicipio("");
    setDepartamento("");
    setSector("");
    fetchCatalog();
  };

  const canClearFilters = hasAppliedFilters || Boolean(especialidad.trim() || municipio.trim() || departamento || sector.trim());
  const municipiosDepto = getMunicipiosByDepartamento(departamento);
  const sectoresMuni = getSectorsForMunicipio(municipio);

  return (
    <main className="account catalog-page">
      <Link className="public-profile-back" to="/cliente">
        <ArrowLeft size={16} />
        Volver a mi espacio de cliente
      </Link>
      <header className="catalog-heading">
        <span className="catalog-eyebrow">CATÁLOGO DE PROFESIONALES</span>
        <h1>Encuentra al experto ideal</h1>
        <p>Busca por especialidad o zona de cobertura para encontrar ayuda cerca de ti.</p>
      </header>

      <section className="catalog-filters" aria-labelledby="catalog-filters-heading">
        <div className="catalog-filters-heading">
          <div>
            <h2 id="catalog-filters-heading"><SlidersHorizontal size={18} aria-hidden="true" /> Personaliza tu búsqueda</h2>
            <p>Completa los campos que necesites y busca profesionales.</p>
          </div>
          <button type="button" className="catalog-reset" onClick={handleClearFilters} disabled={loading || !canClearFilters}>
            <RotateCcw size={15} aria-hidden="true" /> Limpiar filtros
          </button>
        </div>

        <form onSubmit={handleFilterSubmit} className="catalog-filter-form" role="search" aria-label="Buscar técnicos">
          <div className="catalog-filter-field">
            <label htmlFor="catalog-specialty">Especialidad</label>
            <div className="catalog-input-wrap">
              <Wrench size={18} aria-hidden="true" />
              <input id="catalog-specialty" type="search" placeholder="Ej. Aire, Plomería..." value={especialidad} onChange={(e) => setEspecialidad(e.target.value)} />
            </div>
          </div>
          <div className="catalog-filter-field">
            <label htmlFor="catalog-department">Departamento</label>
            <div className="catalog-input-wrap">
              <MapPin size={18} aria-hidden="true" />
              <select
                id="catalog-department"
                value={departamento}
                onChange={(e) => {
                  setDepartamento(e.target.value);
                  setMunicipio("");
                  setSector("");
                }}
              >
                <option value="">Todos</option>
                {departments.map((department) => <option key={department} value={department}>{department}</option>)}
              </select>
            </div>
          </div>
          <div className="catalog-filter-field">
            <label htmlFor="catalog-municipality">Municipio</label>
            <div className="catalog-input-wrap">
              <MapPin size={18} aria-hidden="true" />
              <select
                id="catalog-municipality"
                value={municipio}
                disabled={!departamento}
                onChange={(e) => {
                  setMunicipio(e.target.value);
                  setSector("");
                }}
              >
                <option value="">{departamento ? "Todos los municipios" : "Elige un departamento"}</option>
                {municipiosDepto.map((mun) => (
                  <option key={mun} value={mun}>{mun}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="catalog-filter-field">
            <label htmlFor="catalog-sector">Sector / Colonia</label>
            <div className="catalog-input-wrap">
              <MapPin size={18} aria-hidden="true" />
              <select
                id="catalog-sector"
                value={sector}
                disabled={!municipio}
                onChange={(e) => setSector(e.target.value)}
              >
                <option value="">{municipio ? "Todos los sectores" : "Elige un municipio"}</option>
                {sectoresMuni.map((item) => (
                  <option key={item.sector} value={item.sector}>{item.sector}</option>
                ))}
              </select>
            </div>
          </div>
          <button type="submit" className="button catalog-search-button" disabled={loading}>
            <Search size={18} aria-hidden="true" /> {loading ? "Buscando..." : "Buscar técnicos"}
          </button>
        </form>
      </section>

      {error && <div className="alert catalog-error" role="alert">{error}</div>}

      <section className="catalog-results" aria-labelledby="catalog-results-heading" aria-busy={loading}>
        <div className="catalog-results-heading">
          <h2 id="catalog-results-heading">Profesionales disponibles</h2>
          <span role="status">{loading ? "Buscando profesionales..." : error ? "Búsqueda no completada" : `${technicians.length} ${technicians.length === 1 ? "resultado" : "resultados"}`}</span>
        </div>
        {loading ? (
          <div className="catalog-empty"><Search size={28} aria-hidden="true" /><p>Buscando técnicos para ti...</p></div>
        ) : error ? (
          <div className="catalog-empty">
            <SearchX size={32} aria-hidden="true" />
            <h3>No pudimos cargar los resultados</h3>
            <p>Intenta realizar la búsqueda nuevamente.</p>
            <button type="button" className="button button-outline" onClick={() => fetchCatalog(especialidad, municipio, departamento, sector)}>Reintentar búsqueda</button>
          </div>
        ) : technicians.length === 0 ? (
          <div className="catalog-empty">
            <SearchX size={36} aria-hidden="true" />
            <h3>No encontramos profesionales con esta búsqueda</h3>
            <p>Prueba otra especialidad o zona, o limpia los filtros para ver todos los profesionales disponibles.</p>
            {hasAppliedFilters && <button type="button" className="button button-outline" onClick={handleClearFilters}>Ver todos los profesionales</button>}
          </div>
        ) : (
          <div className="catalog-grid">
            {technicians.map((tech) => <TechnicianCard key={tech.usuario_id} technician={tech} />)}
          </div>
        )}
      </section>
    </main>
  );
}
