import { useRef, useState, type FormEvent } from "react";
import { EmailAuthProvider, reauthenticateWithCredential, signOut, type User } from "firebase/auth";
import { useNavigate } from "react-router-dom";
import { api, errorMessage } from "../lib/api";
import { auth } from "../lib/firebase";
import { Button } from "./ui";

export default function DeleteAccount({ user, pendingFirebaseDeletion = false }: { user: User; pendingFirebaseDeletion?: boolean }) {
  const navigate = useNavigate();
  const pending = useRef(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function remove(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current) return;
    if (!user.email) {
      setError("No pudimos confirmar el correo de esta sesión. Vuelve a iniciar sesión e inténtalo de nuevo.");
      return;
    }
    const password = String(new FormData(event.currentTarget).get("password") ?? "");
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await reauthenticateWithCredential(user, EmailAuthProvider.credential(user.email, password));
      await user.getIdToken(true);
      await api("/users/me", user, undefined, "DELETE");
      if (auth) await signOut(auth);
      navigate("/ingresar", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  return (
    <section className="account-card delete-account" aria-label="Eliminar cuenta">
      {!confirming ? (
        <Button type="button" className="button-outline delete-account-action" onClick={() => setConfirming(true)}>
          {pendingFirebaseDeletion ? "Completar eliminación" : "Eliminar cuenta"}
        </Button>
      ) : (
        <form onSubmit={remove}>
          <h2>Eliminar cuenta</h2>
          <p>Perderás el acceso a todos tus roles de TekniGO y a los entornos que compartan esta cuenta de Firebase. Se conservarán el historial de contrataciones y los registros asociados. Esta acción es permanente.</p>
          <label className="field">
            Confirma tu contraseña
            <input type="password" name="password" autoComplete="current-password" required autoFocus disabled={busy} />
          </label>
          {error && <p className="alert" role="alert">{error}</p>}
          <div className="delete-account-controls">
            <Button type="button" className="button-outline" disabled={busy} onClick={() => {
              setConfirming(false);
              setError("");
            }}>Cancelar</Button>
            <Button type="submit" className="delete-account-action" disabled={busy}>
              {busy ? "Eliminando…" : "Confirmar eliminación"}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
