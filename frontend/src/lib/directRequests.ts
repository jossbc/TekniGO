import { getAuth } from "firebase/auth";

const API_URL = "http://127.0.0.1:8000";

// HU-21: Petición para calcular el presupuesto
export const estimateBudget = async (servicio_id: string, cantidad: number) => {
  const auth = getAuth();
  const token = await auth.currentUser?.getIdToken();

  const response = await fetch(`${API_URL}/direct-requests/estimate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ servicio_id, cantidad }),
  });

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(errorData.detail || "Error al calcular el presupuesto");
  }

  return response.json();
};

// HU-20: Petición para guardar la solicitud
export const createDirectRequest = async (requestData: {
  tecnico_id: string;
  servicio_id: string;
  municipio: string;
  sector: string;
  direccion_exacta: string;
  fecha_hora: string;
  descripcion: string;
  cantidad: number;
  metodo_pago: string;
}) => {
  const auth = getAuth();
  const token = await auth.currentUser?.getIdToken();

  const response = await fetch(`${API_URL}/direct-requests/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(requestData),
  });

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(errorData.detail || "Error al crear la solicitud");
  }

  return response.json();
};
