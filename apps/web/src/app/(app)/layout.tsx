"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { AppChrome } from "@/components/app-chrome";
import { SessionProvider } from "@/components/session-provider";
import { Logo } from "@/components/logo";

/**
 * Everything behind this layout requires a session.
 *
 * The redirect is a convenience, not the control: the API refuses an
 * unauthenticated request and the database refuses a query with no tenant, so
 * a signed-out browser that never reaches this code still gets nothing. What
 * this avoids is a page rendering empty and looking broken.
 */
function SignedOut() {
  const router = useRouter();
  useEffect(() => {
    const timer = setTimeout(() => router.push("/login"), 400);
    return () => clearTimeout(timer);
  }, [router]);
  return (
    <div className="grid min-h-dvh place-items-center">
      <Logo className="text-lg opacity-40" />
    </div>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider fallback={<SignedOut />}>
      <AppChrome>{children}</AppChrome>
    </SessionProvider>
  );
}
