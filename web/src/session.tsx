import { createContext, useContext, type ReactNode } from 'react';
import type { Me } from './types';

type Role = 'viewer' | 'bidder' | 'admin';
const RANK: Record<Role, number> = { viewer: 0, bidder: 1, admin: 2 };

const SessionCtx = createContext<{ user: Me | null; can: (role: Role) => boolean }>({ user: null, can: () => false });

export function SessionProvider({ user, children }: { user: Me | null; children: ReactNode }) {
  const can = (role: Role) => !!user && RANK[user.role] >= RANK[role];
  return <SessionCtx.Provider value={{ user, can }}>{children}</SessionCtx.Provider>;
}

/** Who is signed in, and `can('bidder')` / `can('admin')` for showing or hiding controls. The server enforces every rule; this only keeps the page honest. */
export const useSession = () => useContext(SessionCtx);

/** Shows its children only to people who hold at least this role. */
export function Only({ role, children }: { role: Role; children: ReactNode }) {
  return useSession().can(role) ? <>{children}</> : null;
}

/** Makes everything inside read-only for view-only accounts (a disabled fieldset disables every control in it). */
export function ReadOnly({ children }: { children: ReactNode }) {
  const { can } = useSession();
  if (can('bidder')) return <>{children}</>;
  return (
    <>
      <p className="alert warn" role="note">You have view-only access: you can look at everything but not change it. Ask an administrator if you need to edit.</p>
      <fieldset className="ro" disabled>{children}</fieldset>
    </>
  );
}
