"use client";

/**
 * Who is signed in, for the whole app.
 *
 * Client-side rather than resolved on the server, because the session lives in
 * a cookie the API set for its own origin: in production the app and the API
 * are different hosts, and a server component rendering in Next has no
 * legitimate way to read it. The browser does — it sends it with every
 * credentialed request — so the browser is what asks.
 *
 * The cost is one request on first load, and a moment where the app does not
 * yet know who it is. That is honest: it does not.
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { Role, TierId } from "@mishne/shared";

export interface SessionUser {
  id: string;
  email: string;
  name: string;
  role: Role;
  auth_provider?: string;
}

export interface SessionOrg {
  id: string;
  name: string;
  tier: TierId;
  credit_balance: number;
  credits_held: number;
  retention_days: number;
}

export interface Session {
  user: SessionUser;
  org: SessionOrg;
}

interface SessionState {
  session: Session | null;
  /** True until the first `/auth/me` has answered, either way. */
  loading: boolean;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

const Context = createContext<SessionState | null>(null);

export function SessionProvider({
  children,
  fallback,
}: {
  children: React.ReactNode;
  /** Rendered instead of the app while the session is unknown or absent. */
  fallback?: React.ReactNode;
}) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setSession(await api<Session>("/v1/auth/me"));
    } catch (error) {
      // A 401 is the ordinary signed-out case and not worth surfacing; anything
      // else means the API is unreachable, which the app also cannot fix.
      if (!(error instanceof ApiError)) throw error;
      setSession(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    await api("/v1/auth/logout", { method: "POST" }).catch(() => undefined);
    setSession(null);
    router.push("/login");
  }, [router]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (loading || !session) {
    return <>{fallback ?? null}</>;
  }

  return (
    <Context.Provider value={{ session, loading, refresh, signOut }}>
      {children}
    </Context.Provider>
  );
}

export function useSession(): SessionState & { session: Session } {
  const value = useContext(Context);
  if (!value || !value.session) {
    throw new Error("useSession must be used inside a signed-in SessionProvider");
  }
  return value as SessionState & { session: Session };
}
