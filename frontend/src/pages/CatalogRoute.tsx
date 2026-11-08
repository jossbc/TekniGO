import type { User } from "firebase/auth";
import Header from "../components/Header";
import CatalogPage from "../CatalogPage";

export default function CatalogRoute({ user }: { user: User }) {
  return (
    <div className="site">
      <Header authenticated={true} email={user.email} />
      <CatalogPage user={user} />
    </div>
  );
}

