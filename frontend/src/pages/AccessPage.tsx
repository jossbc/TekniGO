import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { createUserWithEmailAndPassword, EmailAuthProvider, reauthenticateWithCredential, signInWithEmailAndPassword, signOut, type User } from "firebase/auth";
import { ArrowRight, CheckCircle2, ChevronRight, MapPin, ShieldCheck, Users, Wrench } from "lucide-react";
import { auth } from "../lib/firebase";
import { api, errorMessage } from "../lib/api";
import { requestEmailVerification } from "../lib/emailVerification";
import { normalizeHonduranPhone } from "../lib/validation";
import { Brand, Button, Field, PasswordField, PhoneField } from "../components/ui";
import Header from "../components/Header";
import AccessStory from "../components/AccessStory";
import LoadingState from "../components/LoadingState";

type Mode = "login" | "register";

export default function AccessPage({ mode, user }: { mode: Mode; user: User | null }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [role, setRole] = useState<"CLIENTE" | "TECNICO">(location.state?.role === "TECNICO" ? "TECNICO" : "CLIENTE");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
  }, [mode]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    if (mode === "register" && form.get("password") !== form.get("passwordConfirmation")) {
      setError("Las contraseñas no coinciden. Revisa ambos campos.");
      return;
    }
    if (!auth) {
      setError("El acceso aún no está configurado. El equipo debe completar la configuración de Firebase.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const email = String(form.get("email") || "").trim();
      const password = String(form.get("password") || "");
      if (mode === "login") {
        await signInWithEmailAndPassword(auth, email, password);
        navigate("/mi-cuenta", { replace: true });
      } else {
        const phone = normalizeHonduranPhone(String(form.get("phone") || ""));
        if (user) {
          if (!user.email || email.toLowerCase() !== user.email.toLowerCase()) {
            throw new Error("El correo debe coincidir con la cuenta activa. Cierra sesión para registrar otra cuenta.");
          }
          await reauthenticateWithCredential(user, EmailAuthProvider.credential(email, password));
        }
        const identity = user || (await createUserWithEmailAndPassword(auth, email, password)).user;
        await api("/users/register", identity, {
          nombre_completo: form.get("name"),
          dni: form.get("dni"),
          telefono: phone,
          rol: role,
        });
        let emailVerificationNotice = "";
        try {
          if (await requestEmailVerification(identity)) emailVerificationNotice = "Enlace enviado. Revisa tu correo y spam para confirmar tu dirección.";
        } catch (err) {
          emailVerificationNotice = `Tu cuenta se creó, pero no pudimos enviar el enlace: ${errorMessage(err)} Puedes solicitarlo desde esta pantalla.`;
        }
        navigate("/registro/completar", { replace: true, state: { emailVerificationNotice } });
      }
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }
  return (
    <>
      {mode === "register" && busy && <LoadingState variant="screen" message="Creando tu perfil." detail="Estamos guardando tus datos y preparando el siguiente paso. Mantén esta página abierta." />}
      <div className="site" hidden={mode === "register" && busy}>
        <Header mode={mode} authenticated={!!user} showBrand={false} />
        <main className="access-layout">
          <AccessStory />
          <section className="form-panel" aria-labelledby="form-title">
            <div className="form-content">
              <div className="form-brand">
                <Brand />
              </div>
              <h2 id="form-title">{mode === "login" ? "Qué bueno verte de nuevo." : user ? "Completa tu perfil." : "Hagamos que suceda."}</h2>
              <p className="form-description">{mode === "login" ? "Entra a tu cuenta y encuentra tu próxima solución." : "Crea tu cuenta para encontrar ayuda u ofrecer tu talento."}</p>
              <div className="mode-tabs">
                <Link className={mode === "login" ? "active" : ""} to="/ingresar">
                  Iniciar sesión
                </Link>
                <Link className={mode === "register" ? "active" : ""} to="/registro">
                  Crear cuenta
                </Link>
              </div>
              {mode === "login" && user ? (
                <div className="existing-session">
                  <CheckCircle2 size={26} />
                  <h3>Ya tienes una sesión abierta</h3>
                  <p>Continúa a tu cuenta o utiliza el cierre de sesión para cambiar de usuario.</p>
                  <Link className="button" to="/mi-cuenta">
                    Ir a mi cuenta <ArrowRight size={18} />
                  </Link>
                </div>
              ) : (
                <form
                  onSubmit={submit}
                  onInput={(event) => {
                    if (mode !== "register") return;
                    const form = event.currentTarget;
                    const password = form.elements.namedItem("password") as HTMLInputElement;
                    const confirmation = form.elements.namedItem("passwordConfirmation") as HTMLInputElement;
                    confirmation.setCustomValidity(confirmation.value && password.value !== confirmation.value ? "Las contraseñas no coinciden." : "");
                  }}
                >
                  {mode === "register" && (
                    <>
                      <fieldset className="role-picker">
                        <legend>¿Cómo quieres comenzar?</legend>
                        <button type="button" className={role === "CLIENTE" ? "selected" : ""} aria-pressed={role === "CLIENTE"} onClick={() => setRole("CLIENTE")}>
                          <Users size={20} />
                          <span>
                            Busco un servicio<small>Quiero contratar ayuda</small>
                          </span>
                          {role === "CLIENTE" && <CheckCircle2 size={17} />}
                        </button>
                        <button type="button" className={role === "TECNICO" ? "selected" : ""} aria-pressed={role === "TECNICO"} onClick={() => setRole("TECNICO")}>
                          <Wrench size={20} />
                          <span>
                            Ofrezco mis servicios
                            <small>Soy profesional técnico</small>
                          </span>
                          {role === "TECNICO" && <CheckCircle2 size={17} />}
                        </button>
                      </fieldset>
                      <p className="role-note">Una cuenta, ambas posibilidades. Podrás agregar el otro rol después.</p>
                      <Field label="Nombre completo" name="name" autoComplete="name" placeholder="Como aparece en tu identidad" minLength={2} maxLength={150} required />
                      <div className="field-row">
                        <Field label="DNI" name="dni" inputMode="numeric" placeholder="0801-2006-00000" pattern={"[0-9 \\-]{13,17}"} required />
                        <PhoneField />
                      </div>
                    </>
                  )}
                  <Field
                    key={user?.uid || "new-account"}
                    label="Correo electrónico"
                    name="email"
                    type="email"
                    autoComplete="email"
                    placeholder="tu@correo.com"
                    defaultValue={user?.email || ""}
                    readOnly={!!user}
                    required
                    hint={user ? "Este es el correo de tu cuenta activa. Confirma tu contraseña para completar el registro o agregar un rol." : undefined}
                  />
                  <PasswordField key={mode} autoComplete={mode === "register" && !user ? "new-password" : "current-password"} />
                  {mode === "register" && <PasswordField name="passwordConfirmation" label="Confirmar contraseña" autoComplete={user ? "current-password" : "new-password"} />}
                  {mode === "login" && (
                    <div
                      style={{
                        textAlign: "right",
                        marginTop: "-0.5rem",
                        marginBottom: "1rem",
                      }}
                    >
                      <Link to="/recuperar" className="link" style={{ fontSize: "0.875rem" }}>
                        ¿Olvidaste tu contraseña?
                      </Link>
                    </div>
                  )}
                  {mode === "register" && user && (
                    <button
                      className="change-account"
                      type="button"
                      disabled={busy}
                      onClick={async () => {
                        try {
                          await signOut(auth!);
                          setError("");
                        } catch (err) {
                          setError(errorMessage(err));
                        }
                      }}
                    >
                      Usar otro correo para crear una cuenta nueva
                    </button>
                  )}
                  {mode === "register" && (
                    <p className="privacy-note">
                      <ShieldCheck size={16} /> Tu identidad se verificará antes de habilitar contrataciones. Tus documentos no serán públicos.
                    </p>
                  )}
                  {error && (
                    <div className="alert" role="alert">
                      {error}
                    </div>
                  )}
                  <Button type="submit" disabled={busy}>
                    {busy ? "Un momento…" : mode === "login" ? "Entrar a mi cuenta" : "Crear mi perfil"}
                    <ArrowRight size={18} />
                  </Button>
                </form>
              )}
              <p className="switch-copy">
                {mode === "login" ? "¿Es tu primera vez por aquí?" : "¿Ya formas parte de TekniGO?"}{" "}
                <Link to={mode === "login" ? "/registro" : "/ingresar"}>
                  {mode === "login" ? "Crea tu cuenta" : "Inicia sesión"}
                  <ChevronRight size={14} />
                </Link>
              </p>
              <div className="secure-note">
                <ShieldCheck size={16} />
                <span>Tu información, siempre protegida.</span>
              </div>
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
    </>
  );
}

