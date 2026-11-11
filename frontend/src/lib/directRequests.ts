import { getAuth } from "firebase/auth";

const CONFIGURED_API_URL = (import.meta.env?.VITE_API_URL || "").trim().replace(/\/$/, "");
const IS_LOCAL_CONFIG = /^https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?$/i.test(CONFIGURED_API_URL);
// In local Vite dev and Docker nginx, use same-origin relative routes proxied to the backend.
const API_URL = CONFIGURED_API_URL && !IS_LOCAL_CONFIG ? CONFIGURED_API_URL : "";

async function authorizedJsonPost(path: string, body: unknown, fallbackError: string) {
  const currentUser = getAuth().currentUser;
  if (!currentUser) {
    throw new Error("Debes iniciar sesión para continuar.");
  }

  let token = await currentUser.getIdToken();
  const sendRequest = (bearer: string) =>
    fetch(`${API_URL}${path}`, {
      method: "POST",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${bearer}`,
      },
      body: JSON.stringify(body),
    });

  let response = await sendRequest(token);
  if (response.status === 401) {
    token = await currentUser.getIdToken(true);
    response = await sendRequest(token);
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const detail =
      typeof errorData.detail === "string"
        ? errorData.detail
        : Array.isArray(errorData.detail)
          ? errorData.detail
              .map((item: { msg?: string }) => (item.msg || "").replace(/^Value error, /, ""))
              .filter(Boolean)
              .join(". ")
          : "";
    throw new Error(detail || fallbackError);
  }

  return response.json();
}

// HU-21: Petición para calcular el presupuesto
export const estimateBudget = async (servicio_id: string, cantidad: number) =>
  authorizedJsonPost(
    "/direct-requests/estimate",
    { servicio_id, cantidad },
    "Error al calcular el presupuesto",
  );

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
}) =>
  authorizedJsonPost(
    "/direct-requests/",
    requestData,
    "Error al crear la solicitud",
  );
