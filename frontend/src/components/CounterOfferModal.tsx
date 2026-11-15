import { useEffect, useId, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, CheckCircle2, DollarSign, X } from "lucide-react";
import { Button } from "./ui";
import { formatLempiras } from "../lib/clientRequests";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (monto: number, justificacion: string) => Promise<void>;
  solicitudId: string;
  presupuestoOriginal: number;
  especialidadNombre?: string | null;
  municipio?: string | null;
  sector?: string | null;
};

export default function CounterOfferModal({
  isOpen,
  onClose,
  onSubmit,
  presupuestoOriginal,
  especialidadNombre,
  municipio,
  sector,
}: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  const [step, setStep] = useState<"FORM" | "CONFIRM">("FORM");
  const [montoInput, setMontoInput] = useState("");
  const [justificacion, setJustificacion] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setStep("FORM");
      setMontoInput("");
      setJustificacion("");
      setError("");
      setSubmitting(false);
      return;
    }
    const element = dialog.current;
    if (!element) return;
    if (typeof element.showModal === "function") {
      element.showModal();
    }
    document.body.style.overflow = "hidden";
    return () => {
      if (typeof element.close === "function") {
        element.close();
      }
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  if (!isOpen) return null;

  function parseAmount(val: string): number | null {
    const cleaned = val.replace(/[^0-9.]/g, "");
    if (!cleaned) return null;
    const num = parseFloat(cleaned);
    return isNaN(num) || !isFinite(num) ? null : Math.round(num * 100) / 100;
  }

  function handleGoToConfirm() {
    setError("");
    const parsed = parseAmount(montoInput);
    if (parsed === null || parsed <= 0) {
      setError("Ingresa un monto válido mayor a L. 0.00");
      return;
    }
    if (parsed > 999999.99) {
      setError("El monto propuesto no puede exceder L. 999,999.99");
      return;
    }
    setStep("CONFIRM");
  }

  async function handleConfirmSubmit() {
    const parsed = parseAmount(montoInput);
    if (parsed === null) return;
    setError("");
    setSubmitting(true);
    try {
      await onSubmit(parsed, justificacion.trim());
      onClose();
    } catch (err: any) {
      setError(err?.message || "Ocurrió un error al enviar la contraoferta");
      setStep("FORM");
    } finally {
      setSubmitting(false);
    }
  }

  const parsedMonto = parseAmount(montoInput) || 0;
  const diferencia = parsedMonto - presupuestoOriginal;

  return (
    <dialog
      ref={dialog}
      className="dossier-modal client-request-modal"
      aria-labelledby={titleId}
      aria-modal="true"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header className="dossier-modal-heading">
        <h2 id={titleId}>
          {step === "FORM" ? "Proponer otro monto (Contraoferta)" : "Confirmar monto de contraoferta"}
        </h2>
        <button
          type="button"
          className="dossier-modal-close"
          aria-label="Cerrar modal de contraoferta"
          onClick={onClose}
          disabled={submitting}
        >
          <X size={18} />
        </button>
      </header>

      <div className="dossier-modal-body" style={{ padding: "1.25rem" }}>
        {error && (
          <div className="alert" role="alert" style={{ marginBottom: "1rem" }}>
            <AlertCircle size={16} />
            <span>{error}</span>
          </div>
        )}

        {/* Info resumen de la solicitud */}
        <div
          style={{
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            padding: "0.85rem 1rem",
            marginBottom: "1.25rem",
          }}
        >
          <div style={{ fontSize: "0.9rem", color: "var(--muted)", marginBottom: 4 }}>
            {especialidadNombre || "Servicio"} {municipio && `• ${municipio}`}{sector && `, ${sector}`}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "0.9rem" }}>Presupuesto publicado por el cliente:</span>
            <strong style={{ fontSize: "1.05rem", color: "var(--navy)" }}>
              {formatLempiras(presupuestoOriginal)}
            </strong>
          </div>
        </div>

        {step === "FORM" ? (
          <div>
            <div style={{ marginBottom: "1.25rem" }}>
              <label htmlFor="contraoferta-monto" style={{ display: "block", fontWeight: 600, marginBottom: 6 }}>
                Tu monto propuesto (Lempiras) *
              </label>
              <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
                <span style={{ position: "absolute", left: 12, fontWeight: 700, color: "var(--navy)" }}>L.</span>
                <input
                  id="contraoferta-monto"
                  type="number"
                  step="0.01"
                  min="0.01"
                  max="999999.99"
                  placeholder="0.00"
                  value={montoInput}
                  onChange={(e) => {
                    setMontoInput(e.target.value);
                    setError("");
                  }}
                  onKeyDown={(e) => {
                    if (["e", "E", "-", "+"].includes(e.key)) e.preventDefault();
                  }}
                  style={{
                    width: "100%",
                    minHeight: 44,
                    padding: "8px 12px 8px 34px",
                    borderRadius: 8,
                    border: "1px solid #cbd5e1",
                    fontSize: "1.1rem",
                    fontWeight: 600,
                  }}
                  autoFocus
                />
              </div>
              <p style={{ fontSize: "0.8rem", color: "var(--muted)", margin: "4px 0 0" }}>
                El valor debe ser positivo y se registrará con dos decimales en lempiras.
              </p>
            </div>

            <div style={{ marginBottom: "1.5rem" }}>
              <label htmlFor="contraoferta-justificacion" style={{ display: "block", fontWeight: 600, marginBottom: 6 }}>
                Justificación de la propuesta (opcional)
              </label>
              <textarea
                id="contraoferta-justificacion"
                rows={3}
                maxLength={500}
                placeholder="Ej. El trabajo requiere materiales adicionales o inspección previa..."
                value={justificacion}
                onChange={(e) => setJustificacion(e.target.value)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: 8,
                  border: "1px solid #cbd5e1",
                  fontSize: "0.9rem",
                  resize: "vertical",
                }}
              />
              <div style={{ textAlign: "right", fontSize: "0.75rem", color: "var(--muted)" }}>
                {justificacion.length} / 500 caracteres
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
              <Button type="button" className="button-outline" onClick={onClose}>
                Cancelar
              </Button>
              <Button type="button" onClick={handleGoToConfirm}>
                Revisar contraoferta
              </Button>
            </div>
          </div>
        ) : (
          /* PASO 2: Confirmación obligatoria */
          <div>
            <div
              style={{
                background: "#eff6ff",
                border: "1px solid #93c5fd",
                borderRadius: 8,
                padding: "1rem",
                marginBottom: "1.25rem",
              }}
            >
              <h4 style={{ margin: "0 0 8px", fontSize: "1rem", color: "#1e3a8a" }}>
                Resumen de tu contraoferta
              </h4>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <span>Presupuesto original:</span>
                <span>{formatLempiras(presupuestoOriginal)}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <strong>Tu contraoferta:</strong>
                <strong style={{ fontSize: "1.15rem", color: "#1d4ed8" }}>
                  {formatLempiras(parsedMonto)}
                </strong>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem", color: "var(--muted)" }}>
                <span>Diferencia:</span>
                <span style={{ color: diferencia >= 0 ? "#b45309" : "#15803d", fontWeight: 600 }}>
                  {diferencia >= 0 ? `+${formatLempiras(diferencia)}` : `-${formatLempiras(Math.abs(diferencia))}`}
                </span>
              </div>
              {justificacion.trim() && (
                <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #bfdbfe", fontSize: "0.85rem" }}>
                  <span style={{ fontWeight: 600 }}>Justificación:</span>
                  <p style={{ margin: "2px 0 0", fontStyle: "italic" }}>"{justificacion.trim()}"</p>
                </div>
              )}
            </div>

            <p style={{ fontSize: "0.85rem", color: "var(--muted)", margin: "0 0 1.25rem" }}>
              Al confirmar, la licitación pasará al estado <strong>Con ofertas</strong>. El cliente verá tu propuesta y su perfil, pero el trabajo <strong>no se adjudicará automáticamente</strong> hasta que el cliente decida aceptarla.
            </p>

            <div style={{ display: "flex", justifyContent: "space-between", gap: "0.75rem" }}>
              <Button type="button" className="button-outline" onClick={() => setStep("FORM")} disabled={submitting}>
                <ArrowLeft size={15} /> Modificar monto
              </Button>
              <Button type="button" onClick={handleConfirmSubmit} disabled={submitting}>
                <CheckCircle2 size={15} />
                {submitting ? "Enviando contraoferta…" : "Confirmar y enviar contraoferta"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </dialog>
  );
}