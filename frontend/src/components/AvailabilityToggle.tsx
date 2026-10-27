import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import { CheckCircle2, Clock3, Power } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { Button } from "./ui";

export type AvailabilityData = {
  usuario_id: string;
  disponible: boolean;
  estado_verificacion: string;
  puede_recibir_contrataciones: boolean;
};

export function AvailabilityToggle({ user }: { user: User }) {
  const [data, setData] = useState<AvailabilityData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    api<AvailabilityData>("/technicians/me/availability", user)
      .then((res) => {
        if (active) {
          setData(res);
          setError("");
        }
      })
      .catch((err) => {
        if (active) setError(errorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user]);

  async function toggleAvailability() {
    if (!data) return;
    const nextState = !data.disponible;
    setSaving(true);
    setError("");
    setMessage("");

    try {
      const updated = await api<AvailabilityData>("/technicians/me/availability", user, { disponible: nextState }, "PATCH");
      setData(updated);
      setMessage(updated.disponible ? "Estado actualizado a Disponible para recibir solicitudes." : "Estado actualizado a No disponible. No recibirás nuevas contrataciones.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const isAvailable = Boolean(data?.disponible);

  return (
    <section className="account-card" aria-labelledby="availability-heading">
      <span className="step-icon">
        <Power size={22} />
      </span>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.75rem", flexWrap: "wrap" }}>
        <h2 id="availability-heading" style={{ margin: 0 }}>
          Estado de disponibilidad
        </h2>
        <span
          className="status-pill"
          style={{
            background: isAvailable ? "#dcfce7" : "#f1f5f9",
            color: isAvailable ? "#166534" : "#475569",
            fontWeight: 700,
          }}
        >
          {isAvailable ? <CheckCircle2 size={15} /> : <Clock3 size={15} />}
          {loading ? "Cargando…" : isAvailable ? "Disponible" : "No disponible"}
        </span>
      </div>

      <p>
        {isAvailable
          ? "Tu perfil aparece como Disponible. Los clientes pueden enviarte solicitudes si tu cuenta está verificada."
          : "Actualmente estás como No disponible. No podrás recibir nuevas contrataciones ni postulaciones mientras esté desactivado."}
      </p>

      {error && (
        <div className="alert" role="alert" style={{ marginTop: "0.75rem" }}>
          {error}
        </div>
      )}

      {message && (
        <div className="success-alert" role="status" style={{ marginTop: "0.75rem" }}>
          <CheckCircle2 size={18} />
          <span>{message}</span>
        </div>
      )}

      <div style={{ marginTop: "1rem" }}>
        <Button type="button" onClick={toggleAvailability} disabled={loading || saving || !data} className={isAvailable ? "button-outline" : "primary"}>
          {saving ? "Guardando estado…" : isAvailable ? "Cambiar a No disponible" : "Cambiar a Disponible"}
        </Button>
      </div>
    </section>
  );
}
