import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import { AlertTriangle, CheckCircle2, CreditCard, ExternalLink, RefreshCw, ShieldCheck } from "lucide-react";
import { errorMessage } from "../lib/api";
import {
  createDigitalPaymentIntent,
  fetchServiceDigitalPayments,
  formatGatewayEnvironmentBadge,
  formatRetentionLabel,
  getPaymentResultPresentation,
  getRefundStatusPresentation,
  reconcileDigitalPaymentIntent,
  reconcileDigitalPaymentSession,
  retryServiceDigitalRefund,
  type ServiceDigitalPaymentsSummary,
} from "../lib/digitalPayments";
import { Button } from "./ui";

const inflightReturnReconcile = new Map<
  string,
  ReturnType<typeof reconcileDigitalPaymentSession>
>();

function clearPaymentReturnParams(): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  let changed = false;
  for (const key of ["pago", "ref", "session_id"]) {
    if (url.searchParams.has(key)) {
      url.searchParams.delete(key);
      changed = true;
    }
  }
  if (changed) {
    const clean = `${url.pathname}${url.search}${url.hash}`;
    window.history.replaceState(window.history.state, "", clean);
  }
}

type DigitalPaymentStatusPanelProps = {
  user: User;
  modalidad: "DIRECTA" | "ABIERTA";
  solicitudId: string;
  metodoPagoBase: string;
  estadoSolicitud: string;
  onUpdated?: () => void;
};

export default function DigitalPaymentStatusPanel({
  user,
  modalidad,
  solicitudId,
  metodoPagoBase,
  estadoSolicitud,
  onUpdated,
}: DigitalPaymentStatusPanelProps) {
  const [summary, setSummary] = useState<ServiceDigitalPaymentsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function loadSummary() {
    setLoading(true);
    setError("");
    try {
      const data = await fetchServiceDigitalPayments(user, modalidad, solicitudId);
      setSummary(data);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    const params = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
    const pagoFlag = params?.get("pago");
    const sessionId = params?.get("session_id");

    async function syncAndLoad() {
      if (pagoFlag === "cancelado") {
        clearPaymentReturnParams();
        setNotice("El pago fue abandonado o cancelado en la pasarela. No se realizó ningún cargo; puedes reintentarlo.");
      } else if (pagoFlag === "confirmando" && sessionId) {
        try {
          let pending = inflightReturnReconcile.get(sessionId);
          if (!pending) {
            pending = reconcileDigitalPaymentSession(user, sessionId);
            inflightReturnReconcile.set(sessionId, pending);
          }
          const reconciled = await pending;
          if (!active) return;
          inflightReturnReconcile.delete(sessionId);
          clearPaymentReturnParams();
          const ui = getPaymentResultPresentation(reconciled.intento.estado);
          setNotice(`${ui.label}: ${ui.summary}`);
          onUpdated?.();
          return;
        } catch (err) {
          inflightReturnReconcile.delete(sessionId);
          if (active) {
            clearPaymentReturnParams();
            setError(errorMessage(err));
          }
        }
      }
      if (active) {
        await loadSummary();
      }
    }

    void syncAndLoad();
    return () => {
      active = false;
    };
  }, [user, modalidad, solicitudId]);

  async function handleStartCheckout(concepto: "BASE" | "AJUSTE") {
    if (busyAction) return;
    setBusyAction(`pay-${concepto}`);
    setError("");
    setNotice("");
    try {
      const res = await createDigitalPaymentIntent(user, {
        solicitud_id: solicitudId,
        modalidad,
        concepto,
      });
      if (res.intento.checkout_url) {
        window.location.assign(res.intento.checkout_url);
        return;
      }
      setNotice(
        `Intento ${res.intento.referencia_interna} creado en estado ${res.intento.estado} (${res.entorno_pasarela.etiqueta}).`,
      );
      await loadSummary();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyAction("");
    }
  }

  async function handleReconcile(attemptId: string) {
    if (busyAction) return;
    setBusyAction(`reconcile-${attemptId}`);
    setError("");
    setNotice("");
    try {
      const res = await reconcileDigitalPaymentIntent(user, attemptId);
      const ui = getPaymentResultPresentation(res.intento.estado);
      setNotice(`${ui.label}: ${res.intento.motivo_resultado || ui.summary}`);
      await loadSummary();
      onUpdated?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyAction("");
    }
  }

  async function handleRetryRefund() {
    if (busyAction) return;
    setBusyAction("refund-retry");
    setError("");
    setNotice("");
    try {
      const res = await retryServiceDigitalRefund(user, modalidad, solicitudId);
      const ui = getRefundStatusPresentation(res);
      setNotice(`${ui.label}: ${res.ultimo_error || ui.summary}`);
      await loadSummary();
      onUpdated?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyAction("");
    }
  }

  const isCardFlow = (metodoPagoBase || "").toLowerCase() === "tarjeta";
  if (!isCardFlow && !summary?.cobro_base && !summary?.cobro_ajuste && !summary?.reembolso) {
    return null;
  }

  const envBadge = summary ? formatGatewayEnvironmentBadge(summary.entorno_pasarela) : null;
  const baseUi = summary?.cobro_base ? getPaymentResultPresentation(summary.cobro_base.estado) : null;
  const adjUi = summary?.cobro_ajuste ? getPaymentResultPresentation(summary.cobro_ajuste.estado) : null;
  const refundUi = summary?.reembolso ? getRefundStatusPresentation(summary.reembolso) : null;
  const normalizedState = (estadoSolicitud || "").toUpperCase();
  
  const canInitiateBase =
    isCardFlow &&
    !summary?.tiene_pago_base_confirmado &&
    normalizedState === "PENDIENTE" &&
    (!summary?.cobro_base || baseUi?.recoverable);
  
    const hasVisibleContent = Boolean(
    summary?.cobro_base ||
    summary?.cobro_ajuste ||
    summary?.reembolso ||
    canInitiateBase ||
    notice ||
    error,
  );
  if (!loading && !hasVisibleContent) {
    return null;
  }

  return (
    <section
      className="digital-payment-panel"
      aria-label="Estado del pago digital"
      style={{
        marginTop: 14,
        padding: 14,
        borderRadius: 12,
        border: "1px solid #e2e8f0",
        background: "#f8fafc",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <strong style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13.5 }}>
          <CreditCard size={16} aria-hidden="true" />
          Pago digital
        </strong>
        {envBadge && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              padding: "3px 9px",
              borderRadius: 999,
              background: envBadge.tone === "sandbox" ? "#dbeafe" : "#fef3c7",
              color: envBadge.tone === "sandbox" ? "#1d4ed8" : "#92400e",
            }}
          >
            {envBadge.label}
          </span>
        )}
      </div>

      {envBadge && (
        <p style={{ margin: "6px 0 10px", fontSize: 12, color: "#475569", display: "flex", alignItems: "center", gap: 6 }}>
          <ShieldCheck size={14} aria-hidden="true" />
          <span>{envBadge.notice}</span>
        </p>
      )}

      {notice && (
        <p className="success-alert" role="status" style={{ margin: "8px 0" }}>
          <CheckCircle2 size={15} aria-hidden="true" />
          <span>{notice}</span>
        </p>
      )}
      {error && (
        <p className="alert" role="alert" style={{ margin: "8px 0" }}>
          <AlertTriangle size={15} aria-hidden="true" />
          <span>{error}</span>
        </p>
      )}

      {loading && !summary ? (
        <p style={{ fontSize: 12.5, color: "#64748b", margin: 0 }}>Consultando estado del pago digital…</p>
      ) : summary ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {summary.cobro_base && baseUi && (
            <div style={{ padding: 10, borderRadius: 8, background: "#fff", border: "1px solid #e2e8f0" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                <strong>Cobro inicial (Base): L. {summary.cobro_base.monto_intento}</strong>
                <span style={{ fontSize: 12, fontWeight: 700 }}>{baseUi.label}</span>
              </div>
              <p style={{ margin: "4px 0", fontSize: 12, color: "#475569" }}>{baseUi.summary}</p>
              <p style={{ margin: "2px 0", fontSize: 11.5, color: "#64748b" }}>
                Referencia externa: <code>{summary.cobro_base.referencia_externa || "Pendiente"}</code> ·{" "}
                {formatRetentionLabel(summary.cobro_base.estado_retencion)}
              </p>
              {summary.cobro_base.estado === "PENDIENTE" && (
                <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  <Button
                    type="button"
                    className="button-outline"
                    onClick={() => void handleReconcile(summary.cobro_base!.intento_id)}
                    disabled={Boolean(busyAction)}
                  >
                    <RefreshCw size={14} aria-hidden="true" /> Verificar en pasarela
                  </Button>
                </div>
              )}
            </div>
          )}

          {summary.cobro_ajuste && adjUi && (
            <div style={{ padding: 10, borderRadius: 8, background: "#fff", border: "1px solid #e2e8f0" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                <strong>Diferencia de ajuste: L. {summary.cobro_ajuste.monto_intento}</strong>
                <span style={{ fontSize: 12, fontWeight: 700 }}>{adjUi.label}</span>
              </div>
              <p style={{ margin: "4px 0", fontSize: 12, color: "#475569" }}>
                Referencia externa: <code>{summary.cobro_ajuste.referencia_externa || "Pendiente"}</code> ·{" "}
                {formatRetentionLabel(summary.cobro_ajuste.estado_retencion)}
              </p>
              {summary.cobro_ajuste.estado === "PENDIENTE" && (
                <Button
                  type="button"
                  className="button-outline"
                  onClick={() => void handleReconcile(summary.cobro_ajuste!.intento_id)}
                  disabled={Boolean(busyAction)}
                >
                  <RefreshCw size={14} aria-hidden="true" /> Verificar ajuste en pasarela
                </Button>
              )}
            </div>
          )}

          {canInitiateBase && (
            <div>
              <Button
                type="button"
                onClick={() => void handleStartCheckout("BASE")}
                disabled={Boolean(busyAction)}
              >
                <ExternalLink size={14} aria-hidden="true" />
                {busyAction === "pay-BASE"
                  ? "Abriendo pasarela…"
                  : summary.cobro_base
                    ? "Reintentar pago en pasarela"
                    : "Pagar con pasarela digital"}
              </Button>
            </div>
          )}

          {summary.reembolso && refundUi && (
            <div style={{ padding: 10, borderRadius: 8, background: "#fff", border: "1px solid #fecaca" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                <strong>Devolución íntegra: L. {summary.reembolso.monto_reembolso}</strong>
                <span style={{ fontSize: 12, fontWeight: 700 }}>{refundUi.label}</span>
              </div>
              <p style={{ margin: "4px 0", fontSize: 12, color: "#475569" }}>{refundUi.summary}</p>
              <p style={{ margin: "2px 0", fontSize: 11.5, color: "#64748b" }}>
                Comisión: L. {summary.reembolso.monto_comision} · Intentos: {summary.reembolso.intentos} · Ref:{" "}
                <code>{summary.reembolso.referencia_reembolso_pasarela}</code>
              </p>
              {summary.reembolso.ultimo_error && (
                <p style={{ margin: "4px 0", fontSize: 11.5, color: "#b91c1c" }}>
                  Detalle del fallo: {summary.reembolso.ultimo_error}
                </p>
              )}
              {refundUi.canRetry && (
                <Button
                  type="button"
                  className="button-outline"
                  onClick={() => void handleRetryRefund()}
                  disabled={Boolean(busyAction)}
                  style={{ marginTop: 6 }}
                >
                  <RefreshCw size={14} aria-hidden="true" />
                  {busyAction === "refund-retry" ? "Conciliando…" : "Reintentar / Conciliar devolución"}
                </Button>
              )}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}