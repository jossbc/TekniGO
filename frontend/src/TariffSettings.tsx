import React, { useState, type SyntheticEvent } from 'react';
import { auth } from "./lib/firebase";
import { api, errorMessage } from "./lib/api";
import { Button, Field } from "./components/ui";

type PricingUnit = 'POR_HORA' | 'POR_SERVICIO' | 'POR_DIA' | 'POR_PROYECTO';

interface TariffFormValues{
    tarifa_base:number;
    unidad_cobro: PricingUnit;
}

export const TariffSettings: React.FC = () => {
    const [formData, setFormData] = useState<TariffFormValues>({
        tarifa_base: 0,
        unidad_cobro: 'POR_HORA',
    });
    const[loading, setLoading] =useState(false);
    const[message, setMessage] = useState('');
    const[error, setError] = useState('');

    const handleSubmit = async (e: React.SyntheticEvent<HTMLFormElement>) => {
        e.preventDefault();
        if( formData.tarifa_base <= 0){
            setMessage('La tarifa base debe ser mayor a 0');
            return;
        }

        try{
            setLoading(true);
            setMessage('');
            setError('');

            const currentUser = auth?.currentUser;
            if(!currentUser){
                setError('No hay una sesión activa. Inicia sesión nuevamente.');
                return;
            }

            await api('/technicians/me/tariff', currentUser, formData, 'PATCH');

            setMessage('Tarifa actualizada satisfactoriamente.');

        }catch (error){
            setMessage(error instanceof Error ? error.message : 'Error al actualizar la tarifa. Inténtalo de nuevo.');
        } finally{
            setLoading(false);
        }
    };

  return (
    <div className="tariff-container">
      <h2 className="tariff-title">
        Configurar Tarifa Base
      </h2>

      <form onSubmit={handleSubmit} className="tariff-form">
        <div>
          <label className="tariff-label">
            Tarifa Base (Lempiras)
          </label>
          <input
            type="number"
            step="0.01"
            min="0.01"
            value={formData.tarifa_base || ''}
            onChange={(e) => setFormData({ ...formData, tarifa_base: parseFloat(e.target.value) || 0 })}
            className="tariff-input"
            placeholder="0.00"
            required
          />
        </div>

        <div>
          <label className="tariff-label">
            Unidad de Cobro
          </label>
          <select
            value={formData.unidad_cobro}
            onChange={(e) => setFormData({ ...formData, unidad_cobro: e.target.value as PricingUnit })}
            className="tariff-select"
          >
            <option value="POR_HORA">Por Hora</option>
            <option value="POR_SERVICIO">Por Servicio</option>
            <option value="POR_DIA">Por Día</option>
            <option value="POR_PROYECTO">Por Proyecto</option>
          </select>
        </div>

        <button
          type="submit"
          disabled={loading}
          className="tariff-button"
        >
          {loading ? 'Guardando...' : 'Guardar Cambios'}
        </button>

        {error && <p className="tariff-error">{error}</p>}
        {message && <p className="tariff-message">{message}</p>}
      </form>
    </div>
  );
};