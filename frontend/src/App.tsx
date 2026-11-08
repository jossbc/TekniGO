import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { onIdTokenChanged, type User } from "firebase/auth";
import { auth } from "./lib/firebase";
import Header from "./components/Header";
import LoadingState from "./components/LoadingState";
import LandingPage from "./LandingPage";
import CompleteRegistration from "./CompleteRegistration";
import TechnicianPublicProfilePage from "./TechnicianPublicProfilePage";
import AccessPage from "./pages/AccessPage";
import PasswordRecoveryPage from "./pages/PasswordRecoveryPage";
import AccountPage from "./pages/AccountPage";
import VerificationPage from "./pages/VerificationPage";
import ProfileRoute from "./pages/ProfileRoute";
import CatalogRoute from "./pages/CatalogRoute";
import AdminPage from "./pages/AdminPage";

export default function App() {
  const { pathname } = useLocation();
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
  if (!ready && pathname !== "/") return <LoadingState variant="screen" detail="Estamos comprobando tu sesión de forma segura." />;
  const registrationUser = user || auth?.currentUser;
  return (
    <Routes>
      <Route path="/" element={<LandingPage authenticated={!!user} />} />
      <Route path="/ingresar" element={<AccessPage mode="login" user={user} />} />
      <Route path="/registro" element={<AccessPage mode="register" user={user} />} />
      <Route path="/registro/completar" element={registrationUser ? <CompleteRegistration user={registrationUser} /> : <Navigate to="/ingresar" replace />} />
      <Route path="/recuperar" element={<PasswordRecoveryPage user={user} />} />
      <Route path="/mi-cuenta" element={user ? <AccountPage user={user} /> : <Navigate to="/ingresar" replace />} />
      <Route path="/perfil" element={user ? <ProfileRoute user={user} /> : <Navigate to="/ingresar" replace />} />
      <Route path="/catalogo" element={user ? <CatalogRoute user={user} /> : <Navigate to="/ingresar" replace />} />
      <Route
        path="/tecnicos/:id"
        element={
          user ? (
            <div className="site">
              <Header authenticated={true} email={user.email} />
              <TechnicianPublicProfilePage user={user} />
            </div>
          ) : (
            <Navigate to="/ingresar" replace />
          )
        }
      />
      <Route path="/verificacion" element={user ? <VerificationPage user={user} /> : <Navigate to="/ingresar" replace />} />
      <Route path="/admin" element={user ? <AdminPage user={user} /> : <Navigate to="/ingresar" replace />} />
      <Route path="/cliente" element={user ? <AccountPage user={user} space="CLIENTE" /> : <Navigate to="/ingresar" replace />} />
      <Route path="/tecnico" element={user ? <AccountPage user={user} space="TECNICO" /> : <Navigate to="/ingresar" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
