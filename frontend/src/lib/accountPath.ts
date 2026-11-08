import type { Profile } from "./api";

export function accountPath(roles: Profile["roles"]) {
  if (roles.includes("ADMIN")) return "/admin";
  return roles.length === 1 ? (roles[0] === "TECNICO" ? "/tecnico" : "/cliente") : "/mi-cuenta";
}

