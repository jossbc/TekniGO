import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import { Button } from "./ui";
import ModalShell from "./ModalShell";
import { CancelRequestModal } from "./CancellationForms";
import { createDigitalPaymentIntent } from "../lib/digitalPayments";
import { ApiError, errorMessage } from "../lib/api";
import {
  getAdjustmentApi, normalizeState, requestAdjustmentApi, respondAdjustmentApi,
  type AdjustmentRecord, type Rol,
} from "../lib/cancellations";


const MONEY = /^\d{1,6}(\.\d{1,2})?$/;
const toCents = (v: string) => { const [i, f = ""] = v.split("."); return Number(i) * 100 + Number(f.padEnd(2, "0")); };
const fromCents = (c: number) => `${c < 0 ? "-" : ""}${Math.floor(Math.abs(c) / 100)}.${String(Math.abs(c) % 100).padStart(2, "0")}`;
const lempiras = (v: string) => `L. ${v}`;

function AdjustmentRequestModal({ isOpen, onClose, onDone, user, solicitudId, montoBase }: {
  isOpen: boolean; onClose: () => void; onDone: () => void; user: User; solicitudId: string; montoBase: string;
}) {
  const [motivo, setMotivo] = useState("");
  const [total, setTotal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { if (!isOpen) { setMotivo(""); setTotal(""); setError(""); setBusy(false); } }, [isOpen]);

  const validTotal = MONEY.test(total) && toCents(total) > 0 && toCents(total) !== toCents(montoBase);
  const diff = validTotal ? fromCents(toCents(total) - toCents(montoBase)) : null;

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await requestAdjustmentApi(user, solicitudId, { motivo: motivo.trim(), monto_base: montoBase, nuevo_total: total });
      onDone();
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalShell isOpen={isOpen} onClose={onClose} title="Solicitar ajuste de presupuesto">
      {error && <p className="alert" role="alert">{error}</p>}
      <p style={{ fontSize: 14, color: "#475569" }}>
        <strong>Solo puedes presentar un ajuste por servicio</strong>, aunque el cliente lo rechace. El cliente debe aprobarlo antes de continuar y no se cobra nada hasta entonces.
      </p>
      <label htmlFor="adj-base" style={{ display: "block", fontSize: 13, fontWeight: 700, margin: "10px 0 6px" }}>Monto base vigente (L.)</label>
      <input id="adj-base" value={montoBase} readOnly style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid #e2e8f0", background: "#f8fafc" }} />
      <label htmlFor="adj-total" style={{ display: "block", fontSize: 13, fontWeight: 700, margin: "10px 0 6px" }}>Nuevo total (L.)</label>
            <input
        id="adj-total"
        inputMode="decimal"
        placeholder="0.00"
        value={total}
        onChange={(e) => {
          const clean = e.target.value.replace(/,/g, ".");
          if (/^\d{0,6}(\.\d{0,2})?$/.test(clean)) setTotal(clean);
        }}
        style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid #cbd5e1" }}
      />
      {total && !validTotal && <small style={{ color: "#dc2626" }}>Ingresa un total positivo, con máximo 2 decimales y distinto del monto base.</small>}
      {diff && <p style={{ fontSize: 13 }}>Diferencia: <strong>{lempiras(diff)}</strong></p>}
      <label htmlFor="adj-reason" style={{ display: "block", fontSize: 13, fontWeight: 700, margin: "10px 0 6px" }}>Motivo (obligatorio)</label>
      <textarea id="adj-reason" rows={3} maxLength={1500} value={motivo} onChange={(e) => setMotivo(e.target.value)}
        style={{ width: "100%", padding: "10px 12px", borderRadius: 6, border: "1px solid #cbd5e1" }} />
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
        <Button type="button" className="button-outline" onClick={onClose} disabled={busy}>Volver</Button>
        <Button type="button" onClick={submit} disabled={busy || !validTotal || motivo.trim().length < 5}>
          {busy ? "Enviando…" : "Enviar ajuste al cliente"}
        </Button>
      </div>
    </ModalShell>
  );
}


export default function AdjustmentPanel({ user, solicitudId, rol, estado, montoBase, onChanged }: {
  user: User; solicitudId: string; rol: Rol; estado: string; montoBase: string; onChanged?: () => void;
}) {
  const [adjustment, setAdjustment] = useState<AdjustmentRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [comment, setComment] = useState("");
  const [metodoDiferencia, setMetodoDiferencia] = useState<"tarjeta" | "efectivo">("tarjeta");
 
  const [tick, setTick] = useState(0);
  const [showForm, setShowForm] = useState(false);
  const [showCancel, setShowCancel] = useState(false);
  const [notice, setNotice] = useState("");

  const active = normalizeState(estado) === "EN PROCESO";

  useEffect(() => {
    if (!active) return;
    let on = true;
    setLoading(true);
    getAdjustmentApi(user, solicitudId)
      .then((data) => on && setAdjustment(data))
      .catch((err) => {
        if (!on) return;
        if (err instanceof ApiError && err.status === 404) setAdjustment(null);
        else setError(errorMessage(err));
      })
      .finally(() => on && setLoading(false));
    return () => { on = false; };
  }, [user, solicitudId, active, tick]);

  if (!active || loading) return null;

  const refresh = () => { setTick((v) => v + 1); onChanged?.(); };
  const isIncrease = Boolean(adjustment && !adjustment.diferencia.startsWith("-") && toCents(adjustment.diferencia) > 0);
  const initialCard = (adjustment?.metodo_pago_base ?? "efectivo") === "tarjeta";

  async function submitDecision(decision: "APROBAR" | "RECHAZAR", gatewayRef?: string) {
    setBusy(true);
    setError("");
    try {
      const metodo = decision === "APROBAR" && isIncrease
        ? (initialCard ? metodoDiferencia : "efectivo")
        : undefined;
      await respondAdjustmentApi(user, solicitudId, {
        decision,
        comentario: comment.trim() || undefined,
        metodo_pago_diferencia: metodo,
        referencia_pasarela_diferencia: gatewayRef,
      });
      refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleApproveClick() {
    if (isIncrease && initialCard && metodoDiferencia === "tarjeta" && adjustment) {
      setBusy(true);
      setError("");
      try {
        // CA-02 / CA-05: NO aprobar el ajuste antes de que la pasarela confirme el cobro de la diferencia
        const paymentRes = await createDigitalPaymentIntent(user, {
          solicitud_id: solicitudId,
          concepto: "AJUSTE",
        });
        if (paymentRes.intento.checkout_url) {
          window.location.assign(paymentRes.intento.checkout_url);
          return;
        }
        refresh();
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setBusy(false);
      }
      return;
    }
    void submitDecision("APROBAR");
  }

  const split = adjustment?.reparto_financiero;

  return (
    <section className="client-request-box" aria-label="Ajuste de presupuesto">
      <h3>Ajuste de presupuesto</h3>
      {error && <p className="alert" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      {!adjustment && rol === "TECNICO" && (
        <>
          <p>Si el trabajo cambió en sitio, puedes presentar un único ajuste para aprobación del cliente.</p>
          <Button type="button" className="button-outline" onClick={() => setShowForm(true)}>Solicitar ajuste</Button>
        </>
      )}
      {!adjustment && rol === "CLIENTE" && <p>No hay ajustes solicitados.</p>}

      {adjustment && (
        <>
          <p>Motivo: {adjustment.motivo}</p>
          <p>Base {lempiras(adjustment.monto_base)} → nuevo total <strong>{lempiras(adjustment.nuevo_total)}</strong> (diferencia {lempiras(adjustment.diferencia)})</p>
          {adjustment.estado === "PENDIENTE" && rol === "CLIENTE" && (
            <>
              <p><strong>Debes aprobar o rechazar el ajuste</strong> antes de que el servicio continúe. Solo la aprobación cambia el total acordado.</p>
              {isIncrease && initialCard && (
                <div style={{ margin: "10px 0", padding: 10, background: "#f8fafc", borderRadius: 6 }}>
                  <label htmlFor="adj-diff-method" style={{ display: "block", fontSize: 13, fontWeight: 700, marginBottom: 6 }}>
                    Medio de pago para la diferencia ({lempiras(adjustment.diferencia)}):
                  </label>
                  <select
                    id="adj-diff-method"
                    value={metodoDiferencia}
                    onChange={(e) => setMetodoDiferencia(e.target.value as "tarjeta" | "efectivo")}
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid #cbd5e1" }}
                  >
                    <option value="tarjeta">Cobro complementario digital (Tarjeta)</option>
                    <option value="efectivo">Pago en efectivo al técnico por la diferencia</option>
                  </select>
                </div>
              )}
              <input aria-label="Comentario opcional" placeholder="Comentario (opcional)" value={comment} maxLength={500}
                onChange={(e) => setComment(e.target.value)}
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid #cbd5e1", marginBottom: 8 }} />
              <div style={{ display: "flex", gap: 8 }}>
                <Button type="button" disabled={busy} onClick={handleApproveClick}>Aprobar ajuste</Button>
                <Button type="button" className="button-outline" disabled={busy} onClick={() => submitDecision("RECHAZAR")}>Rechazar ajuste</Button>
              </div>
            </>
          )}
          {adjustment.estado === "PENDIENTE" && rol === "TECNICO" && <p>Esperando la respuesta del cliente. No podrás reportar finalización hasta entonces.</p>}
          {adjustment.estado === "APROBADO" && (
            <>
              <p>Ajuste aprobado: el total acordado ahora es <strong>{lempiras(adjustment.nuevo_total)}</strong>.</p>
              {split && (
                <p style={{ fontSize: 13, color: "#334155" }}>
                  Reparto registrado: digital {lempiras(split.cobrado_digital)} · efectivo {lempiras(split.cobrado_efectivo)} · comisión sobre total final {lempiras(split.monto_comision)}.
                </p>
              )}
            </>
          )}
          {adjustment.estado === "RECHAZADO" && (
            <>
              <p>Ajuste rechazado: el total acordado no cambia. Cualquiera de las partes puede cancelar sin penalización.</p>
              <Button type="button" className="button-outline" onClick={() => setShowCancel(true)}>Cancelar sin penalización</Button>
            </>
          )}
        </>
      )}
      <AdjustmentRequestModal isOpen={showForm} onClose={() => setShowForm(false)} onDone={refresh}
        user={user} solicitudId={solicitudId} montoBase={montoBase} />
      <CancelRequestModal isOpen={showCancel} onClose={() => setShowCancel(false)}
        onDone={(msg) => { setNotice(msg); refresh(); }} user={user} solicitudId={solicitudId}
        rol={rol} estado={estado} adjustmentRejected />
    </section>
  );
}