import { useState } from "react";
import type { User } from "firebase/auth";
import Header from "../components/Header";
import { AccountStatus } from "../components/AccountStatus";
import { useAccount } from "../lib/useAccount";
import ProfilePage from "../ProfilePage";

export default function ProfileRoute({ user }: { user: User }) {
  const [name, setName] = useState<string>();
  const { state, retry } = useAccount(user);
  return (
    <div className="site">
      <Header authenticated={true} name={name} email={user.email} />
      {state.status === "ready" ? (
        <ProfilePage user={user} onName={setName} />
      ) : (
        <main className="account">
          <AccountStatus state={state} retry={retry} />
        </main>
      )}
    </div>
  );
}

