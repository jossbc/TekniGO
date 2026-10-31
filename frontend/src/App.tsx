import LoadingState from './components/LoadingState'
import { useEffect, useState, type FormEvent } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { createUserWithEmailAndPassword, EmailAuthProvider, onIdTokenChanged, reauthenticateWithCredential, signInWithEmailAndPassword, signOut, type User, sendPasswordResetEmail } from "firebase/auth";
import { ArrowRight, ArrowUpRight, Check, CheckCircle2, ChevronRight, CircleHelp, Clock3, Hammer, MapPin, ShieldCheck, Sparkles, Users, Wrench } from "lucide-react";
import { auth } from "./lib/firebase";
import { api, errorMessage, type Profile } from "./lib/api";
import { Brand, Button, Field, PasswordField, PhoneField } from "./components/ui";
import { normalizeHonduranPhone } from "./lib/validation";
import ProfilePage from "./ProfilePage";
import { AvailabilityToggle } from "./components/AvailabilityToggle";
import ClientIdentityVerification from "./components/ClientIdentityVerification";
import { useAccount } from "./lib/useAccount";
import { AccountStatus } from "./components/AccountStatus";
import CompleteRegistration from "./CompleteRegistration";
import { registrationComplete, registrationRequirements } from "./lib/registration";

export function accountPath(roles: Profile["roles"]) {
  return roles.length === 1 ? (roles[0] === "TECNICO" ? "/tecnico" : "/cliente") : "/mi-cuenta";
}

type Mode = "login" | "register";
export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(!auth);
  useEffect(
    () =>
      auth
        ? onIdTokenChanged(auth, (value) => {
            setUser(value);
            setReady(true);
          })
        : undefined,
    [],
  );
  if (!ready)
    return (
      <LoadingState variant="screen" detail="Estamos comprobando tu sesión de forma segura." />
    );
  return (
    <Routes>
      <Route path="/" element={<AccessPage mode="login" user={user} />} />
      <Route path="/ingresar" element={<AccessPage mode="login" user={user} />} />
      <Route path="/registro" element={<AccessPage mode="register" user={user} />} />
      <Route path="/registro/completar" element={user ? <CompleteRegistration user={user} /> : <Navigate to="/ingresar" replace />} />
      <Route path="/recuperar" element={<RecuperarContrasena />} />
      <Route path="/mi-cuenta" element={user ? <Account user={user} /> : <Navigate to="/ingresar" replace />} />
      <Route path="/perfil" element={user ? <ProfileRoute user={user} /> : <Navigate to="/ingresar" replace />} />
      <Route path="/verificacion" element={user ? <VerificationRoute user={user} /> : <Navigate to="/ingresar" replace />} />
      <Route path="/cliente" element={user ? <Account user={user} space="CLIENTE" /> : <Navigate to="/ingresar" replace />} />
      <Route path="/tecnico" element={user ? <Account user={user} space="TECNICO" /> : <Navigate to="/ingresar" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );

  function RecuperarContrasena() {
    const [email, setEmail] = useState("");
    const [mensaje, setMensaje] = useState("");
    const [error, setError] = useState("");
    const [cargando, setCargando] = useState(false);

    const manejarEnvio = async (e: React.FormEvent) => {
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
        <Header authenticated={false} />
        <main className="access-layout">
          <section className="story-panel" aria-labelledby="story-title">
            <div className="story-copy">
              <span className="mini-label">EL TALENTO ESTÁ MÁS CERCA DE LO QUE CREES</span>
              <h1 id="story-title">
                Hay alguien
                <br />
                que sabe
                <br />
                <span>cómo resolverlo.</span>
              </h1>
              <p>
                Conectamos lo que necesitas con quienes
                <br className="desktop-break" /> saben hacerlo. Fácil, cercano y en un solo lugar.
              </p>
            </div>
            <div className="trade-scene" aria-hidden="true">
              <div className="scene-grid" />
              <span className="scene-orbit" />
              <div className="tool-tile tile-back">
                <Hammer size={58} strokeWidth={1.4} />
              </div>
              <div className="tool-tile tile-front">
                <Wrench size={67} strokeWidth={1.5} />
              </div>
              <div className="floating-note">
                <span className="note-check">
                  <Check size={17} />
                </span>
                <span>
                  Una buena conexión.<small>Un problema menos.</small>
                </span>
                <Sparkles size={17} />
              </div>
              <span className="scene-dot dot-one" />
              <span className="scene-dot dot-two" />
            </div>
          </section>

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
}

function initials(name?: string, email?: string | null) {
  const parts = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (parts.length > 1) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  if (parts.length === 1) return parts[0][0].toUpperCase();
  return (email?.[0] ?? "?").toUpperCase();
}

function Header({ mode, authenticated, name, email }: { mode?: Mode; authenticated: boolean; name?: string; email?: string | null }) {
  return (
    <header className="header" style={authenticated ? { height: 80 } : undefined}>
      <Brand />
      <nav aria-label="Navegación principal">
        {!authenticated && (
          <a className="nav-help" href="#como-funciona">
            <CircleHelp size={17} /> ¿Cómo funciona?
          </a>
        )}
        {mode && (
          <Link className="nav-action" to={mode === "login" ? "/registro" : "/ingresar"}>
            {mode === "login" ? "Crear una cuenta" : "Iniciar sesión"}
            <ArrowUpRight size={17} />
          </Link>
        )}
        {authenticated && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 14 }}>
            <Link to="/perfil" style={{ color: "#1d4ed8", fontWeight: 600, textDecoration: "none" }}>
              Mi perfil
            </Link>
            <span
              title={email ?? undefined}
              aria-hidden="true"
              style={{ width: 40, height: 40, borderRadius: "50%", background: "#fde8d7", color: "#9a3412", display: "inline-flex", alignItems: "center", justifyContent: "center", fontWeight: 700 }}
            >
              {initials(name, email)}
            </span>
          </span>
        )}
      </nav>
    </header>
  );
}

export function AccessPage({ mode, user }: { mode: Mode; user: User | null }) {
  const navigate = useNavigate();
  const [role, setRole] = useState<"CLIENTE" | "TECNICO">("CLIENTE");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
  }, [mode]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!auth) {
      setError("El acceso aún no está configurado. El equipo debe completar la configuración de Firebase.");
      return;
    }
    const form = new FormData(event.currentTarget);
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
        navigate("/registro/completar", { replace: true });
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="site">
      <Header mode={mode} authenticated={!!user} />
      <main className="access-layout">
        <section className="story-panel" aria-labelledby="story-title">
          <div className="story-copy">
            <span className="mini-label">EL TALENTO ESTÁ MÁS CERCA DE LO QUE CREES</span>
            <h1 id="story-title">
              Hay alguien
              <br />
              que sabe
              <br />
              <span>cómo resolverlo.</span>
            </h1>
            <p>
              Conectamos lo que necesitas con quienes
              <br className="desktop-break" /> saben hacerlo. Fácil, cercano y en un solo lugar.
            </p>
          </div>
          <div className="trade-scene" aria-hidden="true">
            <div className="scene-grid" />
            <span className="scene-orbit" />
            <div className="tool-tile tile-back">
              <Hammer size={58} strokeWidth={1.4} />
            </div>
            <div className="tool-tile tile-front">
              <Wrench size={67} strokeWidth={1.5} />
            </div>
            <div className="floating-note">
              <span className="note-check">
                <Check size={17} />
              </span>
              <span>
                Una buena conexión.<small>Un problema menos.</small>
              </span>
              <Sparkles size={17} />
            </div>
            <span className="scene-dot dot-one" />
            <span className="scene-dot dot-two" />
          </div>
        </section>
        <section className="form-panel" aria-labelledby="form-title">
          <div className="form-content">
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
              <form onSubmit={submit}>
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
                      <Field label="DNI" name="dni" inputMode="numeric" placeholder="0801-2006-00000" pattern="[0-9 -]{13,17}" required />
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
                <PasswordField key={`${mode}-${user?.uid || "new"}`} autoComplete={mode === "register" && !user ? "new-password" : "current-password"} />
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
  );
}

function VerificationRoute({ user }: { user: User }) {
  const { state, retry, updateProfile } = useAccount(user);
  const profile = state.status === 'ready' ? state.profile : null;
  if (state.status === 'loading') return <LoadingState variant="screen" message="Cargando la verificación de identidad." />;
  if (!profile) return <div className="site"><main className="account"><AccountStatus state={state} retry={retry} /></main></div>;
  return (
    <div className="site">
      <Header authenticated={true} name={profile?.nombre_completo} email={user.email} />
      <main className="account" style={{ margin: "0 auto 90px" }}>
        <h1 style={{ marginTop: 0 }}>Verificación de identidad</h1>
        <p>Adjunta tus documentos para validar tu identidad de forma segura en TekniGO.</p>
        <section className="account-card profile-card">
          <ClientIdentityVerification user={user} initialStatus={profile?.estado_identidad} onStatusChange={(estado_identidad) => updateProfile({ ...profile, estado_identidad })} />
        </section>
        <p style={{ marginTop: "1.5rem" }}>
          <Link to="/mi-cuenta" className="link" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            ← Volver a mi cuenta
          </Link>
        </p>
      </main>
    </div>
  );
}

function ProfileRoute({ user }: { user: User }) {
  const [name, setName] = useState<string>();
  const { state, retry } = useAccount(user);
  return (
    <div className="site">
      <Header authenticated={true} name={name} email={user.email} />
      {state.status === 'ready' ? <ProfilePage user={user} onName={setName} /> : (
        <main className="account"><AccountStatus state={state} retry={retry} /></main>
      )}
    </div>
  );
}

function Account({ user, space }: { user: User; space?: "CLIENTE" | "TECNICO" }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { state, retry, updateProfile } = useAccount(user);
  const profile = state.status === 'ready' ? state.profile : null;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [addingRole, setAddingRole] = useState(false);
  const [roleNotice, setRoleNotice] = useState("");
  const [registrationPending, setRegistrationPending] = useState(false);
  const [registrationError, setRegistrationError] = useState("");
  useEffect(() => {
    let active = true;
    setRegistrationPending(false);
    setRegistrationError("");
    if (profile) registrationRequirements(user, profile)
      .then((requirements) => { if (active) setRegistrationPending(!registrationComplete(requirements)); })
      .catch((err) => { if (active) setRegistrationError(errorMessage(err)); });
    return () => { active = false; };
  }, [user, profile]);
  async function addRole() {
    if (!profile || addingRole) return;
    const rol = profile.roles.includes("CLIENTE") ? "TECNICO" : "CLIENTE";
    setAddingRole(true);
    setError("");
    setRoleNotice("");
    try {
      const updated = await api<Profile>("/users/me/roles", user, { rol });
      updateProfile(updated);
      setRoleNotice(`Rol de ${rol === "TECNICO" ? "técnico" : "cliente"} agregado. Ya puedes usar ambos espacios.`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setAddingRole(false);
    }
  }
  if (state.status === "loading") return <LoadingState variant="screen" message="Cargando tu cuenta." detail="Estamos consultando tus datos para mostrarte tu espacio." />;
  if (profile && space && !profile.roles.includes(space)) return <Navigate to={accountPath(profile.roles)} replace state={location.state} />;
  if (profile && !space && profile.roles.length === 1) return <Navigate to={accountPath(profile.roles)} replace state={location.state} />;
  async function logout() {
    setBusy(true);
    setError("");
    try {
      await api("/auth/logout", user, {});
      await signOut(auth!);
      navigate("/ingresar", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="site">
      <Header authenticated={true} name={profile?.nombre_completo} email={user.email} />
      <main className="account">
        <h1>Hola{profile ? `, ${profile.nombre_completo.split(" ")[0]}` : ""}.</h1>
        <p>{space === "TECNICO" ? "Espacio de técnico" : space === "CLIENTE" ? "Espacio de cliente" : "Elige cómo quieres continuar."}</p>
        {profile && profile.roles.length > 1 && (
          <nav className="logout-row" aria-label="Espacios de la cuenta">
            <Link className="button button-outline" to="/cliente">
              Buscar servicios
            </Link>
            <Link className="button button-outline" to="/tecnico">
              Ofrecer servicios
            </Link>
          </nav>
        )}
        {profile && location.state?.registrationSuccess && (
          <div className="success-alert" role="status">
            <CheckCircle2 size={20} />
            <span>Registro completado correctamente. Tu perfil está pendiente de verificación de identidad.</span>
          </div>
        )}
        {error && (
          <div className="alert" role="alert">
            {error}
          </div>
        )}
        <AccountStatus state={state} retry={retry} />
        {registrationPending && <section className="account-card" style={{ marginBottom: 24 }}>
          <h2>Completa tu registro</h2>
          <p>Faltan documentos o datos de tu perfil. Puedes continuar desde donde lo dejaste.</p>
          <Link className="button" to="/registro/completar">Continuar con el paso 2</Link>
        </section>}
        {registrationError && <div className="alert" role="alert">No pudimos comprobar los requisitos de registro: {registrationError} <Link to="/registro/completar">Revisar mi registro</Link></div>}
        {roleNotice && <div className="success-alert" role="status">{roleNotice}</div>}
        {profile && (
          <>
            <div className="account-grid">
              <section className="account-card">
                <span className="step-icon">
                  <Users size={22} />
                </span>
                <h2>Tu cuenta</h2>
                <p>{profile.nombre_completo}</p>
                <p className="email-value">{profile.email}</p>

                <Link to="/perfil">
                  Editar mi perfil <ArrowUpRight size={15} />
                </Link>
                {(!profile.roles.includes("CLIENTE") || !profile.roles.includes("TECNICO")) && (
                  <Button type="button" className="button-outline" onClick={addRole} disabled={addingRole || busy}>
                    {addingRole ? "Agregando rol…" : `Agregar rol de ${profile.roles.includes("CLIENTE") ? "técnico" : "cliente"}`}
                  </Button>
                )}
              </section>
              <section className="account-card">
                <span className="step-icon">
                  <ShieldCheck size={22} />
                </span>
                <h2>{profile.estado_identidad === "VERIFICADO" ? "Identidad verificada" : profile.estado_identidad === "EN_REVISION" ? "Identidad en revisión" : "Verificación pendiente"}</h2>
                <p>{profile.estado_identidad === "VERIFICADO" ? "Tu identidad fue verificada correctamente." : profile.estado_identidad === "EN_REVISION" ? "Recibimos tus documentos. Están pendientes de revisión administrativa." : "Para contratar u ofrecer servicios necesitas completar la verificación de identidad."}</p>
                <span className="status-pill">
                  <Clock3 size={15} /> {profile.estado_identidad === "VERIFICADO" ? "Verificado" : profile.estado_identidad === "EN_REVISION" ? "En revisión" : "Pendiente de verificación"}
                </span>
                <Link to="/verificacion">
                  {profile.estado_identidad === "EN_REVISION" || profile.estado_identidad === "VERIFICADO" ? "Ver estado de identidad" : "Subir DNI y selfie"} <ArrowUpRight size={15} />
                </Link>
              </section>
              {profile.roles.includes("TECNICO") && (space === "TECNICO" || !space) && <AvailabilityToggle user={user} />}
            </div>
          </>
        )}
        <div className="logout-row">
          <Button className="button-outline" onClick={logout} disabled={busy || addingRole}>
            {busy ? "Cerrando sesiones…" : "Cerrar sesion"}
          </Button>
        </div>
      </main>
    </div>
  );
}
