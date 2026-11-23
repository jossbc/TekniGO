import { useMemo, useState } from "react";
import type { User } from "firebase/auth";
import { Button } from "./ui";
import ModalShell from "./ModalShell";
import ReasonEvidenceModal, { type CauseOption } from "./ReasonEvidenceModal";
import { errorMessage } from "../lib/api";
import {
  cancelRequestApi, reopenRequestApi, submitJustificationApi, normalizeState,
  type CancellationCause, type CancellationRecord, type Rol,
} from "../lib/cancellations";

interface CauseRule extends CauseOption {
  when: (ctx: { estado: string; adjustmentRejected: boolean; rol: Rol }) => boolean;
}

// Configuración declarativa (mismo espíritu que la matriz del backend): agregar causa = agregar fila
const CAUSE_RULES: CauseRule[] = [
  {
    value: "AJUSTE_RECHAZADO",
    label: "El ajuste de presupuesto fue rechazado y no deseo continuar",
    when: ({ estado, adjustmentRejected }) => estado === "EN PROCESO" && adjustmentRejected,
  },
  { value: "VOLUNTARIA", label: "Cambio de planes o imprevisto propio", when: () => true },
  {
    value: "INASISTENCIA_CONTRAPARTE",
    label: "La otra parte no se presentó",
    when: ({ estado }) => ["ACEPTADA", "EN PROCESO"].includes(estado),
  },
];

export function cancellationCauses(rol: Rol, estado: string, adjustmentRejected: boolean): CauseOption[] {
  const ctx = { estado: normalizeState(estado), adjustmentRejected, rol };
  return CAUSE_RULES.filter((rule) => rule.when(ctx)).map(({ value, label }) => ({ value, label }));
}

export function CancelRequestModal({ isOpen, onClose, onDone, user, solicitudId, rol, estado, adjustmentRejected = false }: {
  isOpen: boolean; onClose: () => void; onDone: (message: string) => void; user: User;
  solicitudId: string; rol: Rol; estado: string; adjustmentRejected?: boolean;
}) {
  const causes = useMemo(() => cancellationCauses(rol, estado, adjustmentRejected), [rol, estado, adjustmentRejected]);
  const accepted = ["ACEPTADA", "EN PROCESO"].includes(normalizeState(estado));
  return (
    <ReasonEvidenceModal
      isOpen={isOpen} onClose={onClose} user={user} solicitudId={solicitudId}
      title="Cancelar solicitud" causes={causes} submitLabel="Confirmar cancelación"
      intro={
        <>
          <p style={{ margin: "0 0 8px" }}>La cancelación es <strong>definitiva</strong> y queda registrada con tu motivo y evidencia.</p>
          {accepted && rol === "CLIENTE" && (
            <p style={{ margin: 0 }}>Con 24 horas o más de anticipación no se registra falta; con menos, o por inasistencia, se registra un incumplimiento que podrás justificar.</p>
          )}
          {accepted && rol === "TECNICO" && (
            <p style={{ margin: 0 }}>Cancelar un servicio ya aceptado registra un incumplimiento, salvo ajuste rechazado o inasistencia del cliente. Podrás justificarlo ante administración.</p>
          )}
          <p style={{ margin: "8px 0 0" }}>Si hubo pagos digitales, se iniciará la devolución del 100 % sin comisión.</p>
        </>
      }
      onSubmit={async ({ causa, motivo, evidencias }) => {
        const result = await cancelRequestApi(user, solicitudId, { causa: causa as CancellationCause, motivo, evidencias });
        onDone(result.genera_incumplimiento
          ? "Solicitud cancelada. Se registró un incumplimiento pendiente de justificación."
          : "Solicitud cancelada sin penalización.");
      }}
    />
  );
}

export function JustificationModal({ isOpen, onClose, onDone, user, solicitudId }: {
  isOpen: boolean; onClose: () => void; onDone: (message: string) => void; user: User; solicitudId: string;
}) {
  return (
    <ReasonEvidenceModal
      isOpen={isOpen} onClose={onClose} user={user} solicitudId={solicitudId}
      title="Justificar cancelación" submitLabel="Enviar a administración"
      intro="Explica lo ocurrido y adjunta evidencia. Si administración aprueba la justificación, la falta no se contará; el registro original se conserva."
      onSubmit={async ({ motivo, evidencias }) => {
        await submitJustificationApi(user, solicitudId, { motivo, evidencias });
        onDone("Justificación enviada a administración.");
      }}
    />
  );
}

export function ReopenRequestModal({ isOpen, onClose, onDone, user, solicitudId }: {
  isOpen: boolean; onClose: () => void; onDone: (message: string) => void; user: User; solicitudId: string;
}) {
  const [fecha, setFecha] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await reopenRequestApi(user, solicitudId, { fecha_hora: new Date(fecha).toISOString() });
      onDone("Se publicó una nueva licitación enlazada. La solicitud original permanece cancelada.");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalShell isOpen={isOpen} onClose={onClose} title="Reabrir como nueva licitación">
      {error && <p className="alert" role="alert">{error}</p>}
      <p style={{ fontSize: 14, color: "#475569" }}>
        Se creará una licitación nueva con los mismos datos y presupuesto. Elige la nueva fecha y hora del servicio.
      </p>
      <label htmlFor="reopen-date" style={{ display: "block", fontSize: 13, fontWeight: 700, margin: "10px 0 6px" }}>Nueva fecha y hora</label>
      <input id="reopen-date" type="datetime-local" value={fecha} onChange={(e) => setFecha(e.target.value)}
        style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid #cbd5e1" }} />
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
        <Button type="button" className="button-outline" onClick={onClose} disabled={busy}>Volver</Button>
        <Button type="button" onClick={submit} disabled={busy || !fecha}>{busy ? "Publicando…" : "Reabrir licitación"}</Button>
      </div>
    </ModalShell>
  );
}

export type { CancellationRecord };