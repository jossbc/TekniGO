import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import { Link, Navigate, useLocation } from "react-router-dom";
import { ArrowUpRight, CheckCircle2, ClipboardList, Clock3, ShieldCheck, Users } from "lucide-react";
import { api, errorMessage, type Profile } from "../lib/api";
import { accountPath } from "../lib/accountPath";
import { registrationComplete, registrationRequirements } from "../lib/registration";
import { useAccount } from "../lib/useAccount";
import Header from "../components/Header";
import { Button } from "../components/ui";
import LoadingState from "../components/LoadingState";
import { AccountStatus } from "../components/AccountStatus";
import EmailVerification from "../components/EmailVerification";
import PhoneVerification from "../components/PhoneVerification";
import { AvailabilityToggle } from "../components/AvailabilityToggle";
import TechnicianDocumentationCard from "../components/TechnicianDocumentationCard";

export default function AccountPage({ user, space }: { user: User; space?: "CLIENTE" | "TECNICO" }) {
  const location = useLocation();
  const { state, retry, updateProfile } = useAccount(user);
  const profile = state.status === "ready" ? state.profile : null;
  const [error, setError] = useState("");
  const [addingRole, setAddingRole] = useState(false);
  const [roleNotice, setRoleNotice] = useState("");
  const [registrationPending, setRegistrationPending] = useState(false);
  const [registrationError, setRegistrationError] = useState("");
  useEffect(() => {
    let active = true;
    setRegistrationPending(false);
    setRegistrationError("");
    if (profile && !profile.roles.includes("ADMIN"))
      registrationRequirements(user, profile)
        .then((requirements) => {
          if (active) setRegistrationPending(!registrationComplete(requirements));
        })
        .catch((err) => {
          if (active) setRegistrationError(errorMessage(err));
        });
    return () => {
      active = false;
    };
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
  if (profile?.roles.includes("ADMIN")) return <Navigate to="/admin" replace />;
  if (profile && space && !profile.roles.includes(space)) return <Navigate to={accountPath(profile.roles)} replace state={location.state} />;
  if (profile && !space && profile.roles.length === 1) return <Navigate to={accountPath(profile.roles)} replace state={location.state} />;
  return (
    <div className="site">
      <Header authenticated={true} name={profile?.nombre_completo} email={user.email} />
      <main className="account">
        <h1>Hola{profile ? `, ${profile.nombre_completo.split(" ")[0]}` : ""}.</h1>
        <p>{space === "TECNICO" ? "Espacio de técnico" : space === "CLIENTE" ? "Espacio de cliente" : "Elige cómo quieres continuar."}</p>
        <nav className="logout-row" aria-label="Navegación principal" style={{ marginBottom: "2rem", gap: "1rem", flexWrap: "wrap" }}>
          {/* 1. Pantalla de elección (cuando acaba de entrar y tiene 2 roles) */}
          {!space && profile && profile.roles.length > 1 && (
            <>
              <Link className="button button-outline" to="/cliente">
                Entrar como Cliente
              </Link>
              <Link className="button button-outline" to="/tecnico">
                Entrar como Técnico
              </Link>
            </>
          )}

          {/* 2. Espacio de Cliente: Siempre ve 'Buscar servicios' y 'Mis solicitudes'. Si también es técnico, ve 'Cambiar a Técnico' */}
          {space === "CLIENTE" && (
            <>
              <Link className="button button-outline" to="/catalogo">
                Buscar servicios
              </Link>
              <Link className="button button-outline" to="/mis-solicitudes">
                Mis solicitudes
              </Link>
              {profile && profile.roles.includes("TECNICO") && (
                <Link className="button button-outline" to="/tecnico">
                  Cambiar a Técnico
                </Link>
              )}
            </>
          )}

          {/* 3. Espacio de Técnico: Siempre ve 'Ofrecer servicios'. Si también es cliente, ve 'Cambiar a Cliente' */}
          {space === "TECNICO" && (
            <>
              {profile && profile.roles.includes("CLIENTE") && (
                <Link className="button button-outline" to="/cliente">
                  Cambiar a Cliente
                </Link>
              )}
            </>
          )}
        </nav>

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
        {profile?.roles.includes("ADMIN") && <Link className="button" to="/admin">Revisar expedientes técnicos</Link>}
        {registrationPending && (
          <section className="account-card" style={{ marginBottom: 24 }}>
            <h2>Completa tu registro</h2>
            <p>Faltan documentos o datos de tu perfil. Puedes continuar desde donde lo dejaste.</p>
            <Link className="button" to="/registro/completar">
              Continuar con el paso 2
            </Link>
          </section>
        )}
        {registrationError && (
          <div className="alert" role="alert">
            No pudimos comprobar los requisitos de registro: {registrationError} <Link to="/registro/completar">Revisar mi registro</Link>
          </div>
        )}
        {roleNotice && (
          <div className="success-alert" role="status">
            {roleNotice}
          </div>
        )}
        {profile && <EmailVerification user={user} />}
        {profile && <PhoneVerification user={user} />}
        {roleNotice && (
          <div className="success-alert" role="status">
            {roleNotice}
          </div>
        )}
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
                  <Button type="button" className="button-outline" onClick={addRole} disabled={addingRole}>
                    {addingRole ? "Agregando rol…" : `Agregar rol de ${profile.roles.includes("CLIENTE") ? "técnico" : "cliente"}`}
                  </Button>
                )}
              </section>
              <section className="account-card">
                <span className="step-icon">
                  <ShieldCheck size={22} />
                </span>
                <h2>{profile.estado_identidad === "VERIFICADO" ? "Identidad verificada" : profile.estado_identidad === "EN_REVISION" ? "Identidad en revisión" : "Verificación pendiente"}</h2>
                <p>
                  {profile.estado_identidad === "VERIFICADO"
                    ? "Tu identidad fue verificada correctamente."
                    : profile.estado_identidad === "EN_REVISION"
                      ? "Recibimos tus documentos. Están pendientes de revisión administrativa."
                      : "Para contratar u ofrecer servicios necesitas completar la verificación de identidad."}
                </p>
                <span className={`status-pill${profile.estado_identidad === "VERIFICADO" ? " status-pill-verified" : ""}`}>
                  {profile.estado_identidad === "VERIFICADO" ? <CheckCircle2 size={15} aria-hidden="true" /> : <Clock3 size={15} aria-hidden="true" />}{" "}
                  {profile.estado_identidad === "VERIFICADO" ? "Verificado" : profile.estado_identidad === "EN_REVISION" ? "En revisión" : "Pendiente de verificación"}
                </span>
                <Link to="/verificacion">
                  {profile.estado_identidad === "EN_REVISION" || profile.estado_identidad === "VERIFICADO" ? "Ver estado de identidad" : "Subir DNI y selfie"} <ArrowUpRight size={15} />
                </Link>
              </section>
              {profile.roles.includes("CLIENTE") && (space === "CLIENTE" || !space) && (
                <section className="account-card">
                  <span className="step-icon">
                    <ClipboardList size={22} />
                  </span>
                  <h2>Mis solicitudes</h2>
                  <p>
                    Consulta el estado actual de tus contrataciones directas y abiertas, la propuesta pactada y la línea de tiempo de cambios.
                  </p>
                  <Link to="/mis-solicitudes">
                    Ver mis contrataciones <ArrowUpRight size={15} />
                  </Link>
                </section>
              )}
              {profile.roles.includes("TECNICO") && <TechnicianDocumentationCard user={user} />}
              {profile.roles.includes("TECNICO") && (space === "TECNICO" || !space) && <AvailabilityToggle user={user} />}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
