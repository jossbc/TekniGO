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

            const token = await currentUser.getIdToken();

            await api('/technicians/me/tariff', currentUser, {
                method: 'PATCH',
                body: formData,
            });

            setMessage('Tarifa actualizada satisfactoriamente.');

        }catch (error){
            setMessage('Error al actualizar la tarifa. Inténtalo de nuevo.');
        } finally{
            setLoading(false);
        }
    };

  return (
    <div className="max-w-md mx-auto p-6 bg-white rounded-lg shadow-md">
      <h2 className="text-xl font-bold mb-4">Configurar Tarifa Base</h2>
      
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700">Tarifa Base (Lempiras)</label>
          <input
            type="number"
            step="0.01"
            min="0.01"
            value={formData.tarifa_base}
            onChange={(e) => setFormData({ ...formData, tarifa_base: parseFloat(e.target.value) || 0 })}
            className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
            required
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700">Unidad de Cobro</label>
          <select
            value={formData.unidad_cobro}
            onChange={(e) => setFormData({ ...formData, unidad_cobro: e.target.value as PricingUnit })}
            className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
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
          className="w-full bg-indigo-600 text-white py-2 px-4 rounded-md hover:bg-indigo-700 disabled:opacity-50"
        >
          {loading ? 'Guardando...' : 'Guardar Cambios'}
        </button>

        {message && <p className="text-sm text-center mt-2 text-gray-600">{message}</p>}
      </form>
    </div>
  );
};