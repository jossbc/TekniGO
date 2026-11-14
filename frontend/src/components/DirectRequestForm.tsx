import React, { useEffect, useId, useRef, useState } from "react";
import { Calculator, ClipboardList, X } from "lucide-react";
import type { CoverageZone } from "../CoverageZoneSelector";
import { createDirectRequest, estimateBudget } from "../lib/directRequests";

interface DirectRequestFormProps {
  tecnicoId: string;
  coverageZones: CoverageZone[];
  servicioId: string;
  onSuccess: () => void;
  onCancel: () => void;
}

export const DirectRequestForm: React.FC<DirectRequestFormProps> = ({ tecnicoId, coverageZones, servicioId, onSuccess, onCancel }) => {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, []);

  const municipalities = [...new Set(coverageZones.map((zone) => zone.municipio))];
  const [municipio, setMunicipio] = useState("");
  const [sectorPreset, setSectorPreset] = useState("");
  const [customSector, setCustomSector] = useState("");
  const [direccion, setDireccion] = useState("");
  const [fechaHora, setFechaHora] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [cantidad, setCantidad] = useState<number>(1);
  const [metodoPago, setMetodoPago] = useState("Efectivo");

  const availableSectors = [
    ...new Set(
      coverageZones
        .filter((zone) => zone.municipio === municipio && zone.sector.trim())
        .map((zone) => zone.sector.trim()),
    ),
  ];
  const sector = (sectorPreset === "__OTRO__" ? customSector : sectorPreset).trim();

  const [totalEstimado, setTotalEstimado] = useState<number | null>(null);
  const [loadingEstimate, setLoadingEstimate] = useState(false);
  const [loadingSubmit, setLoadingSubmit] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCalculate = async () => {
    if (!Number.isInteger(cantidad) || cantidad < 1 || loadingEstimate) return;
    setLoadingEstimate(true);
    setError(null);
    try {
      const data = await estimateBudget(servicioId, cantidad);
      const total = data?.total_estimado ?? data?.totalEstimado ?? data?.total ?? (typeof data === "number" ? data : null);

      if (total !== null && !isNaN(Number(total))) {
        setTotalEstimado(Number(total));
      } else {
        throw new Error("El servidor no devolvió un cálculo válido.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al calcular el presupuesto");
      setTotalEstimado(null);
    } finally {
      setLoadingEstimate(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loadingSubmit || loadingEstimate || totalEstimado === null || !municipalities.includes(municipio)) return;
    setLoadingSubmit(true);
    setError(null);

    try {
      const requestData = {
        tecnico_id: tecnicoId,
        servicio_id: servicioId,
        municipio,
        sector,
        direccion_exacta: direccion,
        fecha_hora: new Date(fechaHora).toISOString(),
        descripcion,
        cantidad,
        metodo_pago: metodoPago,
      };

      await createDirectRequest(requestData);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al crear la solicitud");
    } finally {
      setLoadingSubmit(false);
    }
  };

  return (
    <dialog ref={dialog} className="direct-request-modal" aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!loadingSubmit) onCancel();
      }}
    >
      <form className="direct-request-form" onSubmit={handleSubmit}>
        <header className="direct-request-heading">
          <span className="step-icon"><ClipboardList size={20} aria-hidden="true" /></span>
          <div>
            <h2 id={titleId}>Solicitar servicio</h2>
            <p>Indica dónde y cuándo necesitas al técnico.</p>
          </div>
          <button type="button" className="dossier-modal-close" aria-label="Cerrar solicitud"
            onClick={onCancel} disabled={loadingSubmit}>
            <X size={20} aria-hidden="true" />
          </button>
        </header>

        <div className="direct-request-body">
          {error && <div className="alert" role="alert">{error}</div>}

          <div className="field-row">
            <label className="field">
              Municipio
              <select
                required
                autoFocus
                value={municipio}
                onChange={(e) => {
                  setMunicipio(e.target.value);
                  setSectorPreset("");
                  setCustomSector("");
                }}
                disabled={!municipalities.length}
              >
                <option value="" disabled>Selecciona un municipio</option>
                {municipalities.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
              {!municipalities.length && <small>Este técnico no tiene municipios de cobertura publicados.</small>}
            </label>
            <label className="field">
              Sector
              <select
                required
                value={sectorPreset}
                onChange={(e) => setSectorPreset(e.target.value)}
                disabled={!municipio}
              >
                <option value="" disabled>
                  {municipio ? "Selecciona un sector" : "Primero elige un municipio"}
                </option>
                {availableSectors.map((sec) => (
                  <option key={sec} value={sec}>{sec}</option>
                ))}
                <option value="__OTRO__">+ Otro sector (escribir manualmente)...</option>
              </select>
              {sectorPreset === "__OTRO__" && (
                <input
                  type="text"
                  required
                  value={customSector}
                  onChange={(e) => setCustomSector(e.target.value)}
                  placeholder="Ej. Colonia Kennedy"
                  style={{ marginTop: 6 }}
                />
              )}
            </label>
          </div>
          <label className="field">
            Dirección exacta
            <input type="text" required value={direccion} onChange={(e) => setDireccion(e.target.value)}
              placeholder="Casa, bloque o punto de referencia" />
          </label>
          <div className="field-row">
            <label className="field">
              Fecha y hora
              <input type="datetime-local" required value={fechaHora} onChange={(e) => setFechaHora(e.target.value)} />
            </label>
            <label className="field">
              Método de pago
              <select value={metodoPago} onChange={(e) => setMetodoPago(e.target.value)}>
                <option value="Efectivo">Efectivo</option>
                <option value="Transferencia">Transferencia</option>
              </select>
            </label>
          </div>
          <label className="field">
            Descripción del problema
            <textarea required rows={3} value={descripcion} onChange={(e) => setDescripcion(e.target.value)}
              placeholder="Describe qué necesitas que revise o repare el técnico." />
          </label>

          <section className="direct-request-budget">
            <h3><Calculator size={17} aria-hidden="true" /> Presupuesto estimado</h3>
            <div className="direct-request-budget-row">
              <label className="field">
                Cantidad
                <input type="number" min="1" required value={cantidad || ""} disabled={loadingEstimate || loadingSubmit}
                  onChange={(e) => {
                    setCantidad(Number(e.target.value));
                    setTotalEstimado(null);
                  }}
                />
              </label>
              <button type="button" className="button button-outline" onClick={handleCalculate}
                disabled={loadingEstimate || loadingSubmit || !Number.isInteger(cantidad) || cantidad < 1}>
                {loadingEstimate ? "Calculando…" : "Calcular"}
              </button>
            </div>
            <div className="direct-request-total" role="status">
              {totalEstimado === null
                ? <span>Calcula el estimado antes de enviar la solicitud.</span>
                : <><span>Total estimado</span><strong>L. {totalEstimado.toFixed(2)}</strong></>}
            </div>
          </section>
        </div>

        <footer className="direct-request-actions">
          <button type="button" className="button button-outline" onClick={onCancel} disabled={loadingSubmit}>Cancelar</button>
          <button type="submit" className="button" disabled={loadingSubmit || loadingEstimate || totalEstimado === null || !municipalities.includes(municipio)}>
            {loadingSubmit ? "Enviando…" : "Confirmar solicitud"}
          </button>
        </footer>
      </form>
    </dialog>
  );
};
