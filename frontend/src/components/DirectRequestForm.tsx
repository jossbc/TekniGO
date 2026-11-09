import React, { useState } from "react";
import { createDirectRequest, estimateBudget } from "../lib/directRequests";

interface DirectRequestFormProps {
  tecnicoId: string;
  servicioId: string;
  onSuccess: () => void;
  onCancel: () => void;
}

export const DirectRequestForm: React.FC<DirectRequestFormProps> = ({ tecnicoId, servicioId, onSuccess, onCancel }) => {
  const [municipio, setMunicipio] = useState("");
  const [sector, setSector] = useState("");
  const [direccion, setDireccion] = useState("");
  const [fechaHora, setFechaHora] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [cantidad, setCantidad] = useState<number>(1);
  const [metodoPago, setMetodoPago] = useState("Efectivo");

  const [totalEstimado, setTotalEstimado] = useState<number | null>(null);
  const [loadingEstimate, setLoadingEstimate] = useState(false);
  const [loadingSubmit, setLoadingSubmit] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCalculate = async () => {
    if (!cantidad || cantidad < 1) return;
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
    } catch (err: any) {
      setError(err.message || "Error al calcular el presupuesto");
      setTotalEstimado(null);
    } finally {
      setLoadingEstimate(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
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

      console.log("Enviando payload al backend:", requestData);
      await createDirectRequest(requestData);
      alert("¡Solicitud enviada exitosamente!");
      onSuccess();
    } catch (err: any) {
      console.error("Rechazo del backend:", err);
      setError(err.message || "Error al crear la solicitud");
    } finally {
      setLoadingSubmit(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 sm:p-6">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg flex flex-col overflow-hidden max-h-[95vh] border border-gray-200">
        {/* Cabecera Azul TekniGO */}
        <div className="bg-blue-600 px-6 py-4 flex justify-between items-center text-white shadow-sm">
          <div>
            <h2 className="text-xl font-bold tracking-wide">Solicitar Servicio</h2>
            <p className="text-blue-100 text-sm mt-0.5">Completa los detalles de tu solicitud</p>
          </div>
          <button onClick={onCancel} className="text-white hover:text-gray-200 bg-blue-700 hover:bg-blue-800 rounded-full p-2 transition-colors">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"></path>
            </svg>
          </button>
        </div>

        {/* Cuerpo scrolleable */}
        <div className="p-6 overflow-y-auto">
          {error && (
            <div className="mb-5 p-4 bg-red-50 border-l-4 border-red-500 rounded-r-lg flex items-start">
              <svg className="w-5 h-5 text-red-500 mr-2 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
              </svg>
              <span className="text-sm text-red-800 font-medium">{error}</span>
            </div>
          )}

          <form id="requestForm" onSubmit={handleSubmit} className="space-y-5">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-1.5">Municipio</label>
                <input
                  type="text"
                  required
                  value={municipio}
                  onChange={(e) => setMunicipio(e.target.value)}
                  className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none text-gray-800 bg-gray-50 focus:bg-white transition-all shadow-sm"
                  placeholder="Ej. Distrito Central"
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-1.5">Sector</label>
                <input
                  type="text"
                  required
                  value={sector}
                  onChange={(e) => setSector(e.target.value)}
                  className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none text-gray-800 bg-gray-50 focus:bg-white transition-all shadow-sm"
                  placeholder="Ej. Colonia Kennedy"
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-1.5">Dirección Exacta</label>
              <input
                type="text"
                required
                value={direccion}
                onChange={(e) => setDireccion(e.target.value)}
                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none text-gray-800 bg-gray-50 focus:bg-white transition-all shadow-sm"
                placeholder="Casa, bloque, referencias..."
              />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-1.5">Fecha y Hora</label>
                <input
                  type="datetime-local"
                  required
                  value={fechaHora}
                  onChange={(e) => setFechaHora(e.target.value)}
                  className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none text-gray-800 bg-gray-50 focus:bg-white transition-all shadow-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-1.5">Método de Pago</label>
                <select
                  value={metodoPago}
                  onChange={(e) => setMetodoPago(e.target.value)}
                  className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none text-gray-800 bg-gray-50 focus:bg-white transition-all shadow-sm cursor-pointer"
                >
                  <option value="Efectivo">Efectivo</option>
                  <option value="Transferencia">Transferencia</option>
                </select>
              </div>
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-1.5">Descripción del problema</label>
              <textarea
                required
                rows={3}
                value={descripcion}
                onChange={(e) => setDescripcion(e.target.value)}
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none text-gray-800 bg-gray-50 focus:bg-white transition-all shadow-sm resize-none"
                placeholder="Describe detalladamente qué necesitas que el técnico repare..."
              ></textarea>
            </div>

            {/* Caja de Presupuesto Destacada */}
            <div className="bg-gradient-to-br from-blue-50 to-blue-100/50 p-5 rounded-xl border border-blue-200/60 shadow-sm">
              <label className="block text-sm font-bold text-blue-900 mb-3">Presupuesto Estimado</label>
              <div className="flex flex-col sm:flex-row gap-3">
                <div className="flex items-center bg-white border border-blue-200 rounded-lg overflow-hidden shadow-sm flex-1">
                  <span className="bg-gray-50 px-3 py-2.5 border-r border-gray-200 text-gray-500 text-sm font-medium">Cant.</span>
                  <input type="number" min="1" required value={cantidad} onChange={(e) => setCantidad(Number(e.target.value))} className="w-full px-4 py-2.5 outline-none text-center font-bold text-gray-800" />
                </div>
                <button
                  type="button"
                  onClick={handleCalculate}
                  disabled={loadingEstimate}
                  className="bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 px-6 rounded-lg transition-all shadow-sm disabled:bg-blue-300 disabled:cursor-not-allowed flex-1 sm:flex-none flex justify-center items-center"
                >
                  {loadingEstimate ? (
                    <svg className="animate-spin h-5 w-5 text-white" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                  ) : (
                    "Calcular"
                  )}
                </button>
              </div>

              {totalEstimado !== null && !isNaN(totalEstimado) && (
                <div className="mt-4 flex justify-between items-end border-t border-blue-200/80 pt-4">
                  <span className="text-sm font-semibold text-blue-800">Total a pagar:</span>
                  <span className="text-2xl font-black text-blue-700 tracking-tight">L. {totalEstimado.toFixed(2)}</span>
                </div>
              )}
            </div>
          </form>
        </div>

        {/* Pie del modal */}
        <div className="px-6 py-5 bg-gray-50 border-t border-gray-200 flex justify-end gap-3 rounded-b-xl">
          <button type="button" onClick={onCancel} className="px-6 py-2.5 text-sm font-bold text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-100 transition-all shadow-sm">
            Cancelar
          </button>
          <button
            type="submit"
            form="requestForm"
            disabled={loadingSubmit || totalEstimado === null}
            className="px-6 py-2.5 text-sm font-bold text-white bg-blue-600 rounded-lg hover:bg-blue-700 focus:ring-4 focus:ring-blue-300 transition-all shadow-md disabled:bg-blue-300 disabled:cursor-not-allowed flex items-center justify-center min-w-[160px]"
          >
            {loadingSubmit ? (
              <>
                <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>{" "}
                Procesando...
              </>
            ) : (
              "Confirmar Solicitud"
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
