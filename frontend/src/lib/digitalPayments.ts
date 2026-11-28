import type { User } from "firebase/auth";
import { api } from "./api";

export type GatewayEnvironment = "SANDBOX" | "SIMULADOR_LOCAL";

export type GatewayRuntimeInfo = {
  proveedor: string;
  entorno: GatewayEnvironment;
  es_sandbox: boolean;
  es_simulacion_local: boolean;
  etiqueta: string;
  moneda: string;
};

export type DigitalPaymentStatus =
  | "PENDIENTE"
  | "CONFIRMADO"
  | "RECHAZADO"
  | "ABANDONADO"
  | "EXPIRADO"
  | "REEMBOLSADO";

export type DigitalRetentionStatus = "NO_APLICA" | "RETENIDO" | "LIBERADO" | "REEMBOLSADO";

export type DigitalPaymentAttempt = {
  id: string;
  clave_idempotencia: string;
  referencia_interna: string;
  modalidad: "DIRECTA" | "ABIERTA";
  solicitud_id: string;
  concepto: "BASE" | "AJUSTE";
  moneda: string;
  monto: string;
  proveedor: string;
  entorno: GatewayEnvironment;
  es_sandbox: boolean;
  es_simulacion_local: boolean;
  referencia_pasarela: string | null;
  payment_intent_id: string | null;
  checkout_url: string | null;
  estado: DigitalPaymentStatus;
  estado_retencion: DigitalRetentionStatus;
  motivo_resultado: string | null;
  creado_en: string | null;
  confirmado_en: string | null;
  liberado_en: string | null;
  reembolsado_en: string | null;
};

export type ServiceDigitalPaymentComponent = {
  intento_id: string;
  concepto: "BASE" | "AJUSTE";
  monto_intento: string;
  monto_confirmado: string;
  moneda: string;
  referencia_interna: string;
  referencia_externa: string | null;
  referencia_pasarela: string | null;
  payment_intent_id: string | null;
  estado: DigitalPaymentStatus;
  estado_retencion: DigitalRetentionStatus;
  proveedor: string;
  entorno: GatewayEnvironment;
  confirmado_en: string | null;
  liberado_en: string | null;
  reembolsado_en: string | null;
};

export type ServiceRefundStatus = {
  id: string;
  solicitud_id: string;
  modalidad: string;
  moneda: string;
  monto_base_reembolsado: string;
  monto_ajuste_reembolsado: string;
  monto_reembolso: string;
  porcentaje_reembolsado: number;
  monto_comision: string;
  referencia_pasarela_origen: string;
  referencia_reembolso_pasarela: string;
  estado_pasarela: "EN_PROCESO_PASARELA" | "PENDIENTE_CONCILIACION" | "CONFIRMADO_PASARELA";
  reembolsado: boolean;
  pendiente_conciliacion: boolean;
  intentos: number;
  ultimo_error: string | null;
  iniciado_en: string | null;
  confirmado_en: string | null;
};

export type ServiceDigitalPaymentsSummary = {
  modalidad: "DIRECTA" | "ABIERTA";
  solicitud_id: string;
  moneda: string;
  metodo_pago_base: string;
  metodo_pago_ajuste: string | null;
  cobro_base: ServiceDigitalPaymentComponent | null;
  cobro_ajuste: ServiceDigitalPaymentComponent | null;
  monto_base_digital_confirmado: string;
  monto_ajuste_digital_confirmado: string;
  monto_digital_confirmado_total: string;
  tiene_pago_base_confirmado: boolean;
  tiene_pago_ajuste_confirmado: boolean;
  reembolso: ServiceRefundStatus | null;
  intentos: DigitalPaymentAttempt[];
  entorno_pasarela: GatewayRuntimeInfo;
};

export type PaymentResultUiPresentation = {
  label: string;
  tone: "pending" | "success" | "danger" | "neutral";
  recoverable: boolean;
  summary: string;
};

export const PAYMENT_RESULT_UI_MAP: Record<DigitalPaymentStatus, PaymentResultUiPresentation> = {
  PENDIENTE: {
    label: "Pendiente de confirmación",
    tone: "pending",
    recoverable: true,
    summary: "El cobro aún no ha sido confirmado por la pasarela. No se acreditan fondos hasta confirmarse.",
  },
  CONFIRMADO: {
    label: "Pago confirmado",
    tone: "success",
    recoverable: false,
    summary: "Cobro verificado por la pasarela y protegido mediante retención lógica hasta completar el servicio.",
  },
  RECHAZADO: {
    label: "Pago rechazado",
    tone: "danger",
    recoverable: true,
    summary: "La pasarela rechazó la transacción. No se realizó ningún cargo; puedes reintentar con otra tarjeta.",
  },
  ABANDONADO: {
    label: "Pago abandonado",
    tone: "neutral",
    recoverable: true,
    summary: "El proceso de pago se cerró antes de completarse. Puedes iniciar un nuevo intento cuando gustes.",
  },
  EXPIRADO: {
    label: "Sesión expirada",
    tone: "neutral",
    recoverable: true,
    summary: "La sesión de pago venció antes de confirmarse. Inicia un nuevo intento para continuar.",
  },
  REEMBOLSADO: {
    label: "Reembolsado",
    tone: "success",
    recoverable: false,
    summary: "La pasarela confirmó la devolución íntegra del monto cobrado, sin comisión.",
  },
};

export type RefundStatusUiPresentation = {
  label: string;
  tone: "pending" | "danger" | "success";
  canRetry: boolean;
  summary: string;
};

export const REFUND_STATUS_UI_MAP: Record<
  ServiceRefundStatus["estado_pasarela"],
  RefundStatusUiPresentation
> = {
  EN_PROCESO_PASARELA: {
    label: "Devolución en proceso",
    tone: "pending",
    canRetry: true,
    summary: "La devolución fue enviada a la pasarela y espera confirmación externa (comisión L. 0.00).",
  },
  PENDIENTE_CONCILIACION: {
    label: "Pendiente de conciliación y reintento",
    tone: "danger",
    canRetry: true,
    summary: "La devolución falló en la pasarela y aún NO ha sido declarada como reembolsada. Puedes reintentarla.",
  },
  CONFIRMADO_PASARELA: {
    label: "Reembolsado",
    tone: "success",
    canRetry: false,
    summary: "Devolución íntegra confirmada por la pasarela (100% sin comisión).",
  },
};

export function getPaymentResultPresentation(status: DigitalPaymentStatus | string): PaymentResultUiPresentation {
  const key = (status || "PENDIENTE").toUpperCase() as DigitalPaymentStatus;
  return PAYMENT_RESULT_UI_MAP[key] ?? PAYMENT_RESULT_UI_MAP.PENDIENTE;
}

export function getRefundStatusPresentation(refund: Pick<ServiceRefundStatus, "estado_pasarela">): RefundStatusUiPresentation {
  return REFUND_STATUS_UI_MAP[refund.estado_pasarela] ?? REFUND_STATUS_UI_MAP.EN_PROCESO_PASARELA;
}

export function formatRetentionLabel(status: DigitalRetentionStatus | string): string {
  switch ((status || "").toUpperCase()) {
    case "RETENIDO":
      return "Retenido hasta completar el servicio";
    case "LIBERADO":
      return "Liberado al cierre del servicio";
    case "REEMBOLSADO":
      return "Devuelto al cliente";
    default:
      return "Sin retención activa";
  }
}

export function formatGatewayEnvironmentBadge(info: GatewayRuntimeInfo): {
  label: string;
  tone: "sandbox" | "simulator";
  notice: string;
} {
  if (info.es_sandbox && !info.es_simulacion_local) {
    return {
      label: info.etiqueta || `Sandbox (${info.proveedor})`,
      tone: "sandbox",
      notice:
        "Entorno de pruebas Sandbox: las transacciones utilizan la API de pruebas con tarjetas ficticias y no mueven dinero real.",
    };
  }
  return {
    label: "Simulador local (Dummy)",
    tone: "simulator",
    notice:
      "Modo simulador local de desarrollo: no corresponde a una integración real de pasarela.",
  };
}

export function fetchGatewayRuntimeInfo(user: User): Promise<GatewayRuntimeInfo> {
  return api<GatewayRuntimeInfo>("/payments/gateway-info", user, undefined, "GET");
}

export function createDigitalPaymentIntent(
  user: User,
  payload: {
    solicitud_id: string;
    modalidad?: "DIRECTA" | "ABIERTA";
    concepto?: "BASE" | "AJUSTE";
    propuesta_id?: string | null;
    idempotency_key?: string;
  },
): Promise<{
  intento: DigitalPaymentAttempt;
  entorno_pasarela: GatewayRuntimeInfo;
  reintento_idempotente: boolean;
}> {
  return api("/payments/intents", user, payload, "POST");
}

export function reconcileDigitalPaymentIntent(
  user: User,
  attemptId: string,
): Promise<{
  conciliado: boolean;
  estado_actualizado: boolean;
  reintento_idempotente: boolean;
  intento: DigitalPaymentAttempt;
  entorno_pasarela: GatewayRuntimeInfo;
}> {
  return api(`/payments/intents/${encodeURIComponent(attemptId)}/reconcile`, user, {}, "POST");
}

export function reconcileDigitalPaymentSession(
  user: User,
  externalReference: string,
): Promise<{
  conciliado: boolean;
  estado_actualizado: boolean;
  reintento_idempotente: boolean;
  intento: DigitalPaymentAttempt;
  entorno_pasarela: GatewayRuntimeInfo;
}> {
  return api(
    "/payments/reconcile-session",
    user,
    { referencia_pasarela: externalReference },
    "POST",
  );
}

export function fetchServiceDigitalPayments(
  user: User,
  modalidad: "DIRECTA" | "ABIERTA",
  solicitudId: string,
): Promise<ServiceDigitalPaymentsSummary> {
  return api<ServiceDigitalPaymentsSummary>(
    `/payments/services/${encodeURIComponent(modalidad)}/${encodeURIComponent(solicitudId)}`,
    user,
    undefined,
    "GET",
  );
}

export function retryServiceDigitalRefund(
  user: User,
  modalidad: "DIRECTA" | "ABIERTA",
  solicitudId: string,
): Promise<ServiceRefundStatus & { reintento_idempotente: boolean }> {
  return api(
    `/payments/services/${encodeURIComponent(modalidad)}/${encodeURIComponent(solicitudId)}/refund/retry`,
    user,
    {},
    "POST",
  );
}