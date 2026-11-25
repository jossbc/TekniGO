import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import { Button } from "./ui";
import { ApiError, errorMessage } from "../lib/api";
import { confirmRefundApi,getCancellationApi, type CancellationRecord, type Rol } from "../lib/cancellations";
import { JustificationModal, ReopenRequestModal } from "./CancellationForms";

const FAULT_TEXT: Record<string, string> = {
  PENDIENTE_JUSTIFICACION: "Incumplimiento registrado, pendiente de justificación.",
  JUSTIFICADA: "Justificación aprobada: la falta no se cuenta.",
  NO_JUSTIFICADA: "Justificación rechazada: la falta se mantiene.",
};

export default function CancellationNotice({ user, solicitudId, rol, onChanged }: {
  user: User; solicitudId: string; rol: Rol; onChanged?: () => void;
}) {
  const [record, setRecord] = useState<CancellationRecord | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tick, setTick] = useState(0);
  const [showJustify, setShowJustify] = useState(false);
  const [showReopen, setShowReopen] = useState(false);

  useEffect(() => {
    let active = true;
    getCancellationApi(user, solicitudId)
      .then((data) => active && setRecord(data))
      .catch((err) => {
        if (!active) return;
        if (err instanceof ApiError && err.status === 404) setRecord(null);
        else setError(errorMessage(err));
      });
    return () => { active = false; };
  }, [user, solicitudId, tick]);

  function done(message: string) {
    setNotice(message);
    setTick((value) => value + 1);
    onChanged?.();
  }

  if (error) return <p className="alert" role="alert">{error}</p>;
  if (!record) return null;

  const mine = record.responsable_rol === rol;
  const canJustify = mine && record.estado_incumplimiento === "PENDIENTE_JUSTIFICACION" && !record.justificacion;
  const canReopen = rol === "CLIENTE" && record.permite_reapertura && !record.solicitud_reabierta_id;

  return (
    <section className="client-request-box" aria-label="Cancelación">
      <h3>Solicitud cancelada</h3>
      <p>Cancelada por {record.autor_rol === "CLIENTE" ? "el cliente" : "el técnico"}. Motivo: {record.motivo}</p>
      {record.genera_incumplimiento
        ? <p><strong>{mine ? "Tienes un incumplimiento: " : "Incumplimiento de la otra parte: "}</strong>{FAULT_TEXT[record.estado_incumplimiento]}</p>
        : <p>Cancelación sin penalización.</p>}
      {record.justificacion && (
        <p>Justificación: <strong>{record.justificacion.estado.toLowerCase()}</strong>
          {record.justificacion.observaciones_admin ? ` · ${record.justificacion.observaciones_admin}` : ""}</p>
      )}
      {record.solicitud_reabierta_id && <p>Ya fue reabierta como nueva licitación.</p>}
      {record.reembolso && (
        <div style={{ margin: "10px 0", padding: 10, background: "#f8fafc", borderRadius: 6, fontSize: 13 }}>
          <strong>Devolución digital del 100 % (sin comisión):</strong> L. {record.reembolso.monto_reembolso} ·{" "}
          <span>
            Estado en pasarela:{" "}
            <strong>
              {record.reembolso.estado_pasarela === "CONFIRMADO_PASARELA"
                ? "Confirmado por la pasarela"
                : "En proceso de confirmación por la pasarela"}
            </strong>
          </span>{" "}
          (Ref. {record.reembolso.referencia_reembolso_pasarela})
          {record.reembolso.estado_pasarela === "EN_PROCESO_PASARELA" && rol === "CLIENTE" && (
            <div style={{ marginTop: 8 }}>
              <Button
                type="button"
                className="button-outline"
                onClick={async () => {
                  try {
                    await confirmRefundApi(user, solicitudId);
                    done("La pasarela confirmó la devolución del 100 % del pago digital.");
                  } catch (err) {
                    setError(errorMessage(err));
                  }
                }}
              >
                Verificar confirmación de pasarela
              </Button>
            </div>
          )}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {canJustify && <Button type="button" className="button-outline" onClick={() => setShowJustify(true)}>Enviar justificación</Button>}
        {canReopen && <Button type="button" onClick={() => setShowReopen(true)}>Reabrir como nueva licitación</Button>}
      </div>
      <JustificationModal isOpen={showJustify} onClose={() => setShowJustify(false)} onDone={done} user={user} solicitudId={solicitudId} />
      <ReopenRequestModal isOpen={showReopen} onClose={() => setShowReopen(false)} onDone={done} user={user} solicitudId={solicitudId} />
    </section>
  );
}