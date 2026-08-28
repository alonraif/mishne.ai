import Link from "next/link";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";

export default function LoginPage() {
  return (
    <div className="grid min-h-dvh lg:grid-cols-2">
      <div className="flex items-center justify-center p-8">
        <div className="w-full max-w-sm space-y-6">
          <Logo className="text-lg" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Pick up where your edit left off.
            </p>
          </div>

          <div className="space-y-4">
            <div className="grid gap-2">
              <Label htmlFor="email">Work email</Label>
              <Input id="email" type="email" placeholder="you@studio.tv" />
            </div>
            <Button className="w-full" asChild>
              <Link href="/projects">Continue</Link>
            </Button>
          </div>

          <div className="flex items-center gap-3">
            <Separator className="flex-1" />
            <span className="text-xs text-muted-foreground">or</span>
            <Separator className="flex-1" />
          </div>

          <Button variant="outline" className="w-full">
            Continue with SSO
          </Button>

          <p className="text-center text-sm text-muted-foreground">
            No account?{" "}
            <Link href="/signup" className="text-foreground underline underline-offset-4">
              Create one
            </Link>
          </p>
        </div>
      </div>

      <div className="hidden items-center border-l border-border bg-card p-12 lg:flex">
        <div className="max-w-md space-y-6">
          <p className="text-2xl font-medium leading-snug tracking-tight">
            Three hours of rushes down to the ten minutes that matter.
          </p>
          <p className="text-sm leading-relaxed text-muted-foreground">
            mishne.ai transcribes your source, makes the selection at the text level, and
            hands back an AAF, FCPXML or EDL that relinks to your own media. It is a rough
            cut, not a fine cut — the heavy lifting, done before you sit down.
          </p>
          <div className="flex gap-2 text-xs text-muted-foreground">
            <span className="rounded-md border border-border px-2 py-1">Avid</span>
            <span className="rounded-md border border-border px-2 py-1">Premiere</span>
            <span className="rounded-md border border-border px-2 py-1">Resolve</span>
            <span className="rounded-md border border-border px-2 py-1">Final Cut</span>
          </div>
        </div>
      </div>
    </div>
  );
}
