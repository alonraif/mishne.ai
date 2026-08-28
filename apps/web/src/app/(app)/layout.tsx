import Link from "next/link";
import { Logo } from "@/components/logo";
import { CreditMeter } from "@/components/credit-meter";
import { mockOrg, mockUser } from "@/lib/mock-data";

export default function AppLayout({ children }: { children: React.ReactNode }) {
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
              href="/billing"
              className="rounded-md px-3 py-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              Billing
            </Link>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <CreditMeter balance={mockOrg.creditBalance} held={mockOrg.creditsHeld} />
            <div className="flex items-center gap-2.5 border-l border-border pl-3">
              <div className="text-right leading-tight">
                <div className="text-sm font-medium">{mockUser.name}</div>
                <div className="text-xs text-muted-foreground">{mockOrg.name}</div>
              </div>
              <div className="grid size-8 place-items-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
                {mockUser.name.split(" ").map((n) => n[0]).join("")}
              </div>
            </div>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1400px] px-6 py-8">{children}</main>
    </div>
  );
}
