import { getAuth, type User } from "firebase/auth";
import { api, resolveApiUrl } from "./api";

export interface TechnicianDirectRequest {
  id: string;
  cliente_nombre: string;
  servicio_nombre: string;
  fecha_hora: string;
  descripcion: string;
  estado: "PENDIENTE" | "ACEPTADA" | "RECHAZADA" | string;
  total_estimado: number | string;
}

export const TECHNICIAN_DIRECT_REQUESTS_PATH = "/technicians/me/direct-requests";

async function authorizedJsonPost(path: string, body: unknown, fallbackError: string) {
  const currentUser = getAuth().currentUser;
  if (!currentUser) {
    throw new Error("Debes iniciar sesión para continuar.");
  }

  let token = await currentUser.getIdToken();
  const sendRequest = (bearer: string) =>
    fetch(resolveApiUrl(path), {
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

// HU-22, HU-23, HU-24: Bandeja, detalle y respuesta del técnico autenticado
export const listTechnicianDirectRequests = (user: User) =>
  api<TechnicianDirectRequest[]>(TECHNICIAN_DIRECT_REQUESTS_PATH, user);

export const getTechnicianDirectRequest = (user: User, solicitudId: string) =>
  api<TechnicianDirectRequest>(`${TECHNICIAN_DIRECT_REQUESTS_PATH}/${encodeURIComponent(solicitudId)}`, user);

export const acceptTechnicianDirectRequest = (user: User, solicitudId: string) =>
  api<TechnicianDirectRequest>(`${TECHNICIAN_DIRECT_REQUESTS_PATH}/${encodeURIComponent(solicitudId)}/accept`, user, undefined, "PATCH");

export const rejectTechnicianDirectRequest = (user: User, solicitudId: string) =>
  api<TechnicianDirectRequest>(`${TECHNICIAN_DIRECT_REQUESTS_PATH}/${encodeURIComponent(solicitudId)}/reject`, user, undefined, "PATCH");
