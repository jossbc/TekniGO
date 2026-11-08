import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { sendPasswordResetEmail, type User } from "firebase/auth";
import { CheckCircle2, MapPin, Users, Wrench } from "lucide-react";
import { auth } from "../lib/firebase";
import { Button, Field } from "../components/ui";
import Header from "../components/Header";
import AccessStory from "../components/AccessStory";

export default function PasswordRecoveryPage({ user }: { user: User | null }) {
  const [email, setEmail] = useState("");
  const [mensaje, setMensaje] = useState("");
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);

  const manejarEnvio = async (e: FormEvent) => {
    e.preventDefault();
    if (!auth) {
      setError("El acceso aún no está configurado. El equipo debe completar la configuración de Firebase.");
      return;
    }

    setMensaje("");
    setError("");
    setCargando(true);

    try {
      await sendPasswordResetEmail(auth, email);
      setMensaje("Enlace enviado. Revisa tu bandeja de entrada o spam.");
      setEmail("");
    } catch (err: any) {
      if (err.code === "auth/user-not-found") {
        setError("No hay ninguna cuenta registrada con ese correo.");
      } else {
        setError("Ocurrió un error. Intenta de nuevo más tarde.");
      }
    } finally {
      setCargando(false);
    }
  };

  return (
    <div className="site">
      <Header authenticated={!!user} email={user?.email} />
      <main className="access-layout">
        <AccessStory />

        <section className="form-panel" aria-labelledby="form-title">
          <div className="form-content">
            <h2 id="form-title">Recuperar contraseña</h2>
            <p className="form-description">Ingresa tu correo electrónico y te enviaremos las instrucciones para crear una nueva contraseña.</p>

            <form onSubmit={manejarEnvio}>
              <Field label="Correo electrónico" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required placeholder="tu@correo.com" autoComplete="email" />

              {mensaje && (
                <div className="success-alert" role="status" style={{ marginTop: "1rem" }}>
                  <CheckCircle2 size={20} />
                  <span>{mensaje}</span>
                </div>
              )}
              {error && (
                <div className="alert" role="alert" style={{ marginTop: "1rem" }}>
                  {error}
                </div>
              )}

              <Button type="submit" disabled={cargando} className="primary" style={{ marginTop: "1.5rem" }}>
                {cargando ? "Enviando enlace..." : "Enviar enlace de recuperación"}
              </Button>
            </form>

            <p className="switch-copy">
              ¿Recordaste tu contraseña?{" "}
              <Link to="/ingresar" className="link">
                Volver a iniciar sesión
              </Link>
            </p>
          </div>
        </section>
      </main>

      <section id="como-funciona" className="how-it-works" aria-label="Cómo funciona">
        <span className="how-heading">
          De una necesidad
          <br />
          <strong>a una solución.</strong>
        </span>
        <div>
          <span className="step-icon">
            <Users size={18} />
          </span>
          <p>
            <strong>01. Crea tu cuenta</strong>
            <small>Un perfil para conectar.</small>
          </p>
        </div>
        <div>
          <span className="step-icon">
            <Wrench size={18} />
          </span>
          <p>
            <strong>02. Encuentra tu conexión</strong>
            <small>Busca ayuda u ofrece tu talento.</small>
          </p>
        </div>
        <div>
          <span className="step-icon">
            <CheckCircle2 size={18} />
          </span>
          <p>
            <strong>03. Manos a la obra</strong>
            <small>Acuerda y resuelve con confianza.</small>
          </p>
        </div>
      </section>
      <footer className="footer">
        <span>© {new Date().getFullYear()} TekniGO</span>
        <span>Talento local. Soluciones reales.</span>
        <span>
          <MapPin size={13} /> Hecho para Honduras
        </span>
      </footer>
    </div>
  );
}
