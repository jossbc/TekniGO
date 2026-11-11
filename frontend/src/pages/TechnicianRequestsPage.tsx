import type { User } from "firebase/auth";
import { ArrowLeft } from "lucide-react";
import { Link, Navigate } from "react-router-dom";
import Header from "../components/Header";
import { AccountStatus } from "../components/AccountStatus";
import { TechnicianRequestsManager } from "../components/TechnicianRequestsManager";
import { accountPath } from "../lib/accountPath";
import { useAccount } from "../lib/useAccount";

export default function TechnicianRequestsPage({ user }: { user: User }) {
  const { state, retry } = useAccount(user);
  const profile = state.status === "ready" ? state.profile : null;

  if (profile?.roles.includes("ADMIN")) return <Navigate to="/admin" replace />;
  if (profile && !profile.roles.includes("TECNICO")) {
    return <Navigate to={accountPath(profile.roles)} replace />;
  }

  return (
    <div className="site">
      <Header
        authenticated={true}
        name={profile?.nombre_completo}
        email={user.email}
        accountHref="/tecnico"
      />
      <main className="client-requests-page">
        <Link to="/tecnico" className="public-profile-back">
          <ArrowLeft size={18} /> Volver al tablero
        </Link>
        {state.status !== "ready" ? (
          <AccountStatus state={state} retry={retry} />
        ) : (
          <TechnicianRequestsManager user={user} />
        )}
      </main>
    </div>
  );
}
