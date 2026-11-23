import { useEffect, useState, type ReactNode } from "react";
import type { User } from "firebase/auth";
import { Button } from "./ui";
import ModalShell from "./ModalShell";
import EvidenceUploader from "./EvidenceUploader";
import { errorMessage } from "../lib/api";
import type { UploadedEvidence } from "../lib/cancellations";

export interface CauseOption { value: string; label: string }

export interface ReasonPayload { causa: string; motivo: string; evidencias: string[] }

export default function ReasonEvidenceModal({
  isOpen, onClose, title, intro, causes, submitLabel, minLength = 10, user, solicitudId, onSubmit,
}: {
  isOpen: boolean; onClose: () => void; title: string; intro: ReactNode;
  causes?: CauseOption[]; submitLabel: string; minLength?: number;
  user: User; solicitudId: string; onSubmit: (value: ReasonPayload) => Promise<void>;
}) {
  const [causa, setCausa] = useState(causes?.[0]?.value ?? "");
  const [motivo, setMotivo] = useState("");
  const [evidencias, setEvidencias] = useState<UploadedEvidence[]>([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (isOpen) return;
    setCausa(causes?.[0]?.value ?? "");
    setMotivo("");
    setEvidencias([]);
    setError("");
    setSubmitting(false);
  }, [isOpen, causes]);

  const length = motivo.trim().length;

  async function handleSubmit() {
    setSubmitting(true);
    setError("");
    try {
      await onSubmit({ causa, motivo: motivo.trim(), evidencias: evidencias.map((item) => item.referencia) });
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <ModalShell isOpen={isOpen} onClose={onClose} title={title}>
      {error && <p className="alert" role="alert">{error}</p>}
      <div style={{ fontSize: 14, color: "#475569", marginBottom: 14 }}>{intro}</div>

      {causes && causes.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <label htmlFor="reason-cause" style={{ display: "block", fontSize: 13, fontWeight: 700, marginBottom: 6 }}>Causa</label>
          <select id="reason-cause" value={causa} onChange={(e) => setCausa(e.target.value)}
            style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid #cbd5e1" }}>
            {causes.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </div>
      )}

      <div style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <label htmlFor="reason-text" style={{ fontSize: 13, fontWeight: 700 }}>Motivo (obligatorio)</label>
          <span style={{ fontSize: 12, color: length >= minLength ? "#16a34a" : "#dc2626" }}>
            {length} / {minLength} mínimo
          </span>
        </div>
        <textarea id="reason-text" rows={4} maxLength={1500} value={motivo}
          onChange={(e) => setMotivo(e.target.value)}
          style={{ width: "100%", padding: "10px 12px", borderRadius: 6, border: "1px solid #cbd5e1" }} />
      </div>

      <EvidenceUploader user={user} solicitudId={solicitudId} value={evidencias}
        onChange={setEvidencias} disabled={submitting} />

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
        <Button type="button" className="button-outline" onClick={onClose} disabled={submitting}>Volver</Button>
        <Button type="button" onClick={handleSubmit} disabled={submitting || length < minLength}>
          {submitting ? "Enviando…" : submitLabel}
        </Button>
      </div>
    </ModalShell>
  );
}