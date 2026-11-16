import { useRef, useState } from "react";
import type { User } from "firebase/auth";
import { api, errorMessage } from "../lib/api";
import { Button } from "./ui";
import StartServiceConfirmation from "./StartServiceConfirmation";

export default function StartServiceAction({
  user, path, paymentMethod, cardPaymentConfirmed, onStarted,
}: {
  user: User;
  path: string;
  paymentMethod: string;
  cardPaymentConfirmed?: boolean;
  onStarted: () => void;
}) {
  const pending = useRef(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const needsPayment = paymentMethod.trim().toUpperCase() === "TARJETA" && !cardPaymentConfirmed;

  async function start() {
    if (pending.current || needsPayment) return;
    setConfirming(false);
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await api(path, user, undefined, "POST");
      onStarted();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  return (
    <div>
      <Button type="button" onClick={() => setConfirming(true)} disabled={needsPayment || busy}>
        {busy ? "Iniciando…" : "Iniciar servicio"}
      </Button>
      {confirming && (
        <StartServiceConfirmation onCancel={() => setConfirming(false)} onConfirm={() => void start()} />
      )}
      {needsPayment && <p>El cobro con tarjeta debe estar confirmado antes de iniciar.</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
