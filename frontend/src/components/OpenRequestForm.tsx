import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import { Link } from "react-router-dom";
import { CheckCircle2, Eye, Info, Lock, MapPin, ShieldAlert } from "lucide-react";
import { Button } from "./ui";
import { api, errorMessage } from "../lib/api";
import { formatLempiras, formatUnitLabel } from "../lib/clientRequests";
import {
  fetchCoverageZonesCatalog,
  fetchSpecialtiesForOpenRequest,
  fetchSuggestedRangeForSpecialty,
  publishOpenRequestApi,
  type PublishedOpenRequestResponse,
  type SpecialtyOption,
  type SuggestedRangeResponse,
  type TechnicianCoverageZoneItem,
} from "../lib/openRequests";
import {
  getHondurasDepartamentos,
  getMunicipiosByDepartamento,
  getSectorsForMunicipio,
} from "../data/hondurasSectors";

type HiringEligibility = {
  puede_contratar: boolean;
  identidad_verificada: boolean;
  correo_verificado: boolean;
  telefono_verificado: boolean;
  motivos: string[];
};

export default function OpenRequestForm({
  user,
  initialSpecialties,
  initialEligibility,
  onPublished,
}: {
  user: User;
  initialSpecialties?: SpecialtyOption[];
  initialEligibility?: HiringEligibility | null;
  onPublished?: (created: PublishedOpenRequestResponse) => void;
}) {
  const [specialties, setSpecialties] = useState<SpecialtyOption[]>(initialSpecialties || []);
  const [eligibility, setEligibility] = useState<HiringEligibility | null>(
    initialEligibility !== undefined ? initialEligibility : null,
  );
  const [loadingInit, setLoadingInit] = useState(
    initialSpecialties === undefined || initialEligibility === undefined,
  );

  const [especialidadId, setEspecialidadId] = useState(initialSpecialties?.[0]?.id || "");
  const [descripcion, setDescripcion] = useState("");
  const [departamento, setDepartamento] = useState("");
  const [municipio, setMunicipio] = useState("");
  const [sectorPreset, setSectorPreset] = useState("");
  const [customSector, setCustomSector] = useState("");
  const [coverageCatalog, setCoverageCatalog] = useState<TechnicianCoverageZoneItem[]>([]);
  const [direccionExacta, setDireccionExacta] = useState("");
  const [fechaHora, setFechaHora] = useState("");
  const [metodoPago, setMetodoPago] = useState<"efectivo" | "tarjeta">("efectivo");
  const [presupuesto, setPresupuesto] = useState("");

  const sector = (sectorPreset === "__OTRO__" ? customSector : sectorPreset).trim();

  const [suggestedRange, setSuggestedRange] = useState<SuggestedRangeResponse | null>(null);
  const [loadingRange, setLoadingRange] = useState(false);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<PublishedOpenRequestResponse | null>(null);

  useEffect(() => {
    if (initialSpecialties !== undefined && initialEligibility !== undefined) return;
    let active = true;
    setLoadingInit(true);
    Promise.all([
      initialSpecialties !== undefined
        ? Promise.resolve(initialSpecialties)
        : fetchSpecialtiesForOpenRequest(user),
      initialEligibility !== undefined
        ? Promise.resolve(initialEligibility)
        : api<HiringEligibility>("/users/me/hiring-eligibility", user).catch(() => null),
    ])
      .then(([loadedSpecialties, loadedEligibility]) => {
        if (!active) return;
        setSpecialties(loadedSpecialties);
        if (!especialidadId && loadedSpecialties.length > 0) {
          setEspecialidadId(loadedSpecialties[0].id);
        }
        setEligibility(loadedEligibility);
      })
      .catch((err) => {
        if (active) setError(errorMessage(err));
      })
      .finally(() => {
        if (active) setLoadingInit(false);
      });
    return () => {
      active = false;
    };
  }, [user, initialSpecialties, initialEligibility]);

  useEffect(() => {
    let active = true;
    if (!especialidadId) {
      setSuggestedRange(null);
      fetchCoverageZonesCatalog(user).then((zones) => {
        if (active) setCoverageCatalog(zones);
      });
      return;
    }
    setLoadingRange(true);
    Promise.all([
      fetchSuggestedRangeForSpecialty(user, especialidadId),
      fetchCoverageZonesCatalog(user, { especialidad_id: especialidadId }),
    ])
      .then(([range, zones]) => {
        if (!active) return;
        setSuggestedRange(range);
        setCoverageCatalog(zones);
      })
      .finally(() => {
        if (active) setLoadingRange(false);
      });
    return () => {
      active = false;
    };
  }, [user, especialidadId]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError("");

    if (!especialidadId) {
      setError("Selecciona la especialidad requerida para tu solicitud.");
      return;
    }
    if (descripcion.trim().length < 10) {
      setError("Describe el trabajo con al menos 10 caracteres.");
      return;
    }
    if (!municipio.trim() || !sector.trim()) {
      setError("Completa el municipio y el sector de ubicación general.");
      return;
    }
    if (direccionExacta.trim().length < 5) {
      setError("Ingresa la dirección exacta privada con al menos 5 caracteres.");
      return;
    }
    if (!fechaHora) {
      setError("Selecciona la fecha y hora futura en que necesitas el servicio.");
      return;
    }
    const parsedDate = new Date(fechaHora);
    if (Number.isNaN(parsedDate.getTime()) || parsedDate.getTime() <= Date.now()) {
      setError("La fecha y hora programadas deben ser futuras.");
      return;
    }
    const numericAmount = Number(presupuesto);
    if (!presupuesto || Number.isNaN(numericAmount) || numericAmount <= 0) {
      setError("Ingresa un monto ofrecido mayor que cero.");
      return;
    }

    setSubmitting(true);
    try {
      const response = await publishOpenRequestApi(user, {
        especialidad_id: especialidadId,
        descripcion: descripcion.trim(),
        municipio: municipio.trim(),
        sector: sector.trim(),
        direccion_exacta: direccionExacta.trim(),
        fecha_hora: parsedDate.toISOString(),
        metodo_pago: metodoPago,
        presupuesto: numericAmount,
      });
      setCreated(response);
      onPublished?.(response);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  function resetFormForAnother() {
    setCreated(null);
    setDescripcion("");
    setDireccionExacta("");
    setFechaHora("");
    setPresupuesto("");
    setError("");
  }

  if (loadingInit) {
    return <p role="status">Comprobando requisitos y catálogo de especialidades…</p>;
  }

  if (created) {
    return (
      <section className="account-card open-request-confirmation" role="status" aria-live="polite">
        <div className="open-request-confirmation-head">
          <span className="step-icon">
            <CheckCircle2 size={24} />
          </span>
          <div>
            <span className="request-modality-badge request-modality-abierta">Modalidad Abierta</span>{" "}
            <span className="request-status-badge request-status-aceptada">{created.estado}</span>
            <h2>Solicitud abierta publicada correctamente</h2>
          </div>
        </div>

        <p>
          Tu oferta quedó registrada en estado <strong>Publicada</strong> y sin técnico adjudicado. Los técnicos
          verificados y disponibles de la especialidad y zona compatibles ya pueden ver el municipio y sector para
          enviarte propuestas.
        </p>

        <dl className="open-request-confirmation-details">
          <div>
            <dt>Especialidad</dt>
            <dd>{created.especialidad?.nombre || "Seleccionada"}</dd>
          </div>
          <div>
            <dt>Ubicación visible a postulantes</dt>
            <dd>
              {created.municipio}, {created.sector}
            </dd>
          </div>
          <div>
            <dt>Fecha y hora programada</dt>
            <dd>
              {created.fecha_hora
                ? new Date(created.fecha_hora).toLocaleString("es-HN", {
                    dateStyle: "medium",
                    timeStyle: "short",
                  })
                : "Registrada"}
            </dd>
          </div>
          <div>
            <dt>Presupuesto ofrecido</dt>
            <dd>{formatLempiras(created.presupuesto)}</dd>
          </div>
        </dl>

        <div className="logout-row" style={{ marginTop: 20, gap: 12, flexWrap: "wrap" }}>
          <Link className="button" to={`/mis-solicitudes/${encodeURIComponent(created.id)}`}>
            Ver seguimiento de esta solicitud
          </Link>
          <Button type="button" className="button-outline" onClick={resetFormForAnother}>
            Publicar otra solicitud abierta
          </Button>
        </div>
      </section>
    );
  }

  return (
    <section className="account-card open-request-form-card">
      {eligibility && !eligibility.puede_contratar && (
        <div className="alert open-request-eligibility-alert" role="alert">
          <ShieldAlert size={20} />
          <div>
            <strong>Debes completar tu verificación antes de publicar una solicitud abierta:</strong>
            <ul>
              {eligibility.motivos.map((motivo, idx) => (
                <li key={idx}>{motivo}</li>
              ))}
            </ul>
            <Link to="/cliente">Ir a mi cuenta para completar la verificación</Link>
          </div>
        </div>
      )}

      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}

      <form className="open-request-form" onSubmit={handleSubmit} noValidate>
        {/* Bloque 1: Especialidad y descripción del trabajo */}
        <fieldset className="open-request-fieldset">
          <legend>1. Qué trabajo necesitas</legend>

          <div className="field">
            <label htmlFor="open-req-specialty">Especialidad requerida</label>
            <select
              id="open-req-specialty"
              required
              value={especialidadId}
              onChange={(event) => setEspecialidadId(event.target.value)}
            >
              <option value="">Selecciona una especialidad</option>
              {specialties.map((spec) => (
                <option key={spec.id} value={spec.id}>
                  {spec.nombre}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="open-req-description">Descripción detallada del trabajo</label>
            <textarea
              id="open-req-description"
              required
              rows={4}
              value={descripcion}
              onChange={(event) => setDescripcion(event.target.value)}
              placeholder="Explica qué necesitas reparar o instalar, materiales disponibles y cualquier detalle importante..."
            />
          </div>
        </fieldset>

        {/* Bloque 2 (ST-02 / CA-03): Ubicación parcial visible a técnicos elegibles */}
        <fieldset className="open-request-fieldset open-request-location-public">
          <legend>
            <Eye size={16} aria-hidden="true" /> 2. Ubicación general en Honduras (visible para técnicos elegibles)
          </legend>
          <p className="open-request-privacy-note">
            <MapPin size={15} aria-hidden="true" />
            <span>
              Selecciona tu departamento, municipio y sector usando el mismo catálogo de zonas de cobertura de los técnicos. Los postulantes verán <strong>únicamente el municipio y el sector</strong> antes de adjudicar.
            </span>
          </p>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
              gap: "14px",
            }}
          >
            <div className="field">
              <label htmlFor="open-req-departamento">Departamento</label>
              <select
                id="open-req-departamento"
                required
                value={departamento}
                onChange={(event) => {
                  setDepartamento(event.target.value);
                  setMunicipio("");
                  setSectorPreset("");
                  setCustomSector("");
                }}
              >
                <option value="">Selecciona un departamento</option>
                {getHondurasDepartamentos().map((depto) => (
                  <option key={depto} value={depto}>
                    {depto}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label htmlFor="open-req-municipio">Municipio</label>
              <select
                id="open-req-municipio"
                required
                disabled={!departamento}
                value={municipio}
                onChange={(event) => {
                  setMunicipio(event.target.value);
                  setSectorPreset("");
                  setCustomSector("");
                }}
              >
                <option value="">
                  {departamento ? "Selecciona un municipio" : "Primero elige un departamento"}
                </option>
                {getMunicipiosByDepartamento(departamento).map((mun) => (
                  <option key={mun} value={mun}>
                    {mun}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label htmlFor="open-req-sector">Sector o colonia</label>
              <select
                id="open-req-sector"
                required
                disabled={!municipio}
                value={sectorPreset}
                onChange={(event) => setSectorPreset(event.target.value)}
              >
                <option value="">
                  {municipio ? "Selecciona un sector o colonia" : "Primero elige un municipio"}
                </option>
                {getSectorsForMunicipio(municipio, coverageCatalog).map((item) => (
                  <option key={item.sector} value={item.sector}>
                    {item.sector}
                    {item.hasTechnicianCoverage ? " (Con cobertura de técnicos)" : ""}
                  </option>
                ))}
                <option value="__OTRO__">Otro (escribir sector manualmente)...</option>
              </select>

              {sectorPreset === "__OTRO__" && (
                <input
                  id="open-req-sector-custom"
                  type="text"
                  required
                  value={customSector}
                  onChange={(event) => {
                    const sinNumero = event.target.value.replace(/^\d+/, "");
                    const capitalizado = sinNumero
                      ? sinNumero.charAt(0).toUpperCase() + sinNumero.slice(1)
                      : "";
                    setCustomSector(capitalizado);
                  }}
                  placeholder="Escribe el nombre del sector o colonia..."
                  style={{ marginTop: 8 }}
                />
              )}
            </div>
          </div>
        </fieldset>

        {/* Bloque 3 (ST-02 / CA-01 / CA-03): Dirección exacta privada separada */}
        <fieldset className="open-request-fieldset open-request-location-private">
          <legend>
            <Lock size={16} aria-hidden="true" /> 3. Dirección exacta privada (oculta hasta adjudicar)
          </legend>
          <p className="open-request-privacy-note open-request-privacy-note-locked">
            <Lock size={15} aria-hidden="true" />
            <span>
              <strong>Dato privado:</strong> ningún técnico postulante tendrá acceso a tu dirección exacta ni a tu teléfono mientras la solicitud esté publicada. Solo se compartirá con el técnico que tú selecciones.
            </span>
          </p>

          <div className="field">
            <label htmlFor="open-req-direccion-privada">Dirección exacta y referencias de llegada</label>
            <textarea
              id="open-req-direccion-privada"
              required
              rows={2}
              value={direccionExacta}
              onChange={(event) => setDireccionExacta(event.target.value)}
              placeholder="Bloque, número de casa, color de portón o punto de referencia exacto..."
            />
          </div>
        </fieldset>

        {/* Bloque 4: Fecha futura, método de pago, rango sugerido y monto ofrecido */}
        <fieldset className="open-request-fieldset">
          <legend>4. Programación y presupuesto ofrecido</legend>

          <div className="open-request-two-cols">
            <div className="field">
              <label htmlFor="open-req-fecha">Fecha y hora requerida</label>
              <input
                id="open-req-fecha"
                type="datetime-local"
                required
                value={fechaHora}
                onChange={(event) => setFechaHora(event.target.value)}
              />
            </div>

            <div className="field">
              <label htmlFor="open-req-metodo-pago">Método de pago</label>
              <select
                id="open-req-metodo-pago"
                value={metodoPago}
                onChange={(event) => setMetodoPago(event.target.value as "efectivo" | "tarjeta")}
              >
                <option value="efectivo">Efectivo</option>
                <option value="tarjeta">Tarjeta</option>
              </select>
            </div>
          </div>

          {/* CA-05 y CA-06: Referencia de rango sugerido que no sustituye el monto del cliente */}
          {especialidadId && (
            <div className="open-request-suggested-range" role="status" aria-live="polite">
              <Info size={16} aria-hidden="true" />
              <div>
                <strong>Referencia de tarifas de la categoría</strong>
                {loadingRange ? (
                  <p>Consultando tarifas promedio de servicios activos en esta especialidad…</p>
                ) : suggestedRange && suggestedRange.tiene_referencia ? (
                  <p>
                    Rango sugerido:{" "}
                    <strong>
                      {formatLempiras(suggestedRange.rango_sugerido_min)} –{" "}
                      {formatLempiras(suggestedRange.rango_sugerido_max)}
                    </strong>{" "}
                    (tarifa base promedio {formatLempiras(suggestedRange.tarifa_promedio)}{" "}
                    {formatUnitLabel(suggestedRange.unidad_cobro)}, calculada sobre{" "}
                    {suggestedRange.servicios_comparables}{" "}
                    {suggestedRange.servicios_comparables === 1 ? "servicio activo" : "servicios activos"}). Esta
                    referencia es orientativa y no reemplaza el monto que elijas ofrecer.
                  </p>
                ) : (
                  <p>
                    {suggestedRange?.mensaje ||
                      "Aún no hay suficientes tarifas comparables registradas en esta especialidad para sugerir un rango. Puedes definir libremente el monto que deseas ofrecer."}
                  </p>
                )}
              </div>
            </div>
          )}

          <div className="field">
            <label htmlFor="open-req-presupuesto">Monto ofrecido (Lempiras)</label>
            <input
              id="open-req-presupuesto"
              type="number"
              min="1"
              step="0.01"
              required
              value={presupuesto}
              onChange={(event) => setPresupuesto(event.target.value)}
              placeholder="Ej. 850.00"
            />
          </div>
        </fieldset>

        <div className="open-request-actions">
          <Button
            type="submit"
            disabled={submitting || Boolean(eligibility && !eligibility.puede_contratar)}
          >
            {submitting ? "Publicando solicitud…" : "Publicar solicitud abierta"}
          </Button>
        </div>
      </form>
    </section>
  );
}
