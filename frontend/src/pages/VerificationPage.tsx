import type { User } from "firebase/auth";
import { Link } from "react-router-dom";
import Header from "../components/Header";
import LoadingState from "../components/LoadingState";
import { AccountStatus } from "../components/AccountStatus";
import EmailVerification from "../components/EmailVerification";
import PhoneVerification from "../components/PhoneVerification";
import ClientIdentityVerification from "../components/ClientIdentityVerification";
import TechnicianVerificationForm from "../components/TechnicianVerificationForm";
import { useAccount } from "../lib/useAccount";

export default function VerificationPage({ user }: { user: User }) {
  const { state, retry, updateProfile } = useAccount(user);
  const profile = state.status === "ready" ? state.profile : null;

  if (state.status === "loading") return <LoadingState variant="screen" message="Cargando la verificación de identidad." />;
  if (!profile)
    return (
      <div className="site">
        <main className="account">
          <AccountStatus state={state} retry={retry} />
        </main>
      </div>
    );

  return (
    <div className="site">
      <Header authenticated={true} name={profile?.nombre_completo} email={user.email} />
      <p style={{ marginTop: "1.5rem" }}>
          <Link to="/mi-cuenta" className="link" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            ← Volver a mi cuenta
          </Link>
        </p>
      <main className="account" style={{ margin: "0 auto 90px" }}>
        <h1 style={{ marginTop: 0 }}>{profile.roles.includes("TECNICO") ? "Documentos y antecedentes del técnico" : "Verificación de identidad"}</h1>
        <p>{profile.roles.includes("TECNICO") ? "Completa los documentos pendientes de tu perfil técnico. Los documentos de identidad ya aprobados se conservan." : "Adjunta tus documentos para validar tu identidad de forma segura en TekniGO."}</p>
        <EmailVerification user={user} />
        <PhoneVerification user={user} />
        <section className="account-card profile-card">
          {profile?.roles.includes("TECNICO") ? (
            <TechnicianVerificationForm user={user} initialStatus={profile?.estado_identidad} onStatusChange={(estado) => updateProfile({ ...profile, estado_identidad: estado })} />
          ) : (
            <ClientIdentityVerification user={user} initialStatus={profile?.estado_identidad} onStatusChange={(estado_identidad) => updateProfile({ ...profile, estado_identidad })} />
          )}
        </section>
        
      </main>
    </div>
  );
}

