import { Logo } from "@/components/logo";
import { SignupFlow } from "@/components/signup-flow";
import Link from "next/link";

export default function SignupPage() {
  return (
    <div className="min-h-dvh">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-[1100px] items-center justify-between px-6">
          <Logo />
          <Link href="/login" className="text-sm text-muted-foreground hover:text-foreground">
            Sign in
          </Link>
        </div>
      </header>
      <main className="mx-auto max-w-[1100px] px-6 py-10">
        <SignupFlow />
      </main>
    </div>
  );
}
