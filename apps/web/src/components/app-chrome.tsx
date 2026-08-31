"use client";

/**
 * The signed-in header: who you are, which org you are in, and the way out.
 *
 * A client component because the session is only known in the browser (see
 * `session-provider`), and because signing out is an action rather than a link.
 */

import Link from "next/link";
import { LogOut } from "lucide-react";
import { Logo } from "@/components/logo";
import { CreditMeter } from "@/components/credit-meter";
import { useSession } from "@/components/session-provider";
import { Button } from "@/components/ui/button";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  member: "Member",
  // Worth showing: it is the role that explains why the upload button is not
  // there, and a permission that is invisible reads as a broken page.
  viewer: "View only",
};

export function AppChrome({ children }: { children: React.ReactNode }) {
  const { session, signOut } = useSession();
  const initials =
    session.user.name
      .split(" ")
      .map((part) => part[0])
      .filter(Boolean)
      .join("")
      .slice(0, 2)
      .toUpperCase() || session.user.email.slice(0, 2).toUpperCase();

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-40 border-b border-border bg-background/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-6 px-6">
          <Link href="/projects">
            <Logo />
          </Link>
          <nav className="flex items-center gap-1 text-sm">
            <Link
              href="/projects"
              className="rounded-md px-3 py-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              Projects
            </Link>
            <Link
              href="/team"
              className="rounded-md px-3 py-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              Team
            </Link>
            <Link
              href="/billing"
              className="rounded-md px-3 py-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              Billing
            </Link>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <CreditMeter
              balance={session.org.credit_balance}
              held={session.org.credits_held}
            />
            <div className="flex items-center gap-2.5 border-l border-border pl-3">
              <div className="text-right leading-tight">
                <div className="text-sm font-medium">
                  {session.user.name || session.user.email}
                </div>
                <div className="text-xs text-muted-foreground">
                  {session.org.name} · {ROLE_LABEL[session.user.role] ?? session.user.role}
                </div>
              </div>
              <div className="grid size-8 place-items-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
                {initials}
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void signOut()}
                aria-label="Sign out"
              >
                <LogOut className="size-4" />
              </Button>
            </div>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1400px] px-6 py-8">{children}</main>
    </div>
  );
}
