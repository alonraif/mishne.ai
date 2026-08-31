"use client";

/**
 * Accepting an invitation.
 *
 * Outside the `(app)` group on purpose: that layout requires a session, and
 * the person holding this link does not have one and is not a member of
 * anything yet.
 *
 * The page asks the API what the link is for before showing a form. A page
 * that renders "set your password" for an expired link wastes somebody's time
 * and then fails; showing the organisation and the address it was sent to also
 * lets them notice it is the wrong address before they type anything.
 */

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Logo } from "@/components/logo";
import { ApiError } from "@/lib/api";
import { apiSend } from "@/lib/dto";
import { useApi } from "@/lib/use-api";

interface Preview {
  orgName: string;
  email: string;
  role: string;
  expiresAt: string;
}

export default function AcceptInvitePage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const invitation = useApi<Preview>(`/v1/auth/invitations/${token}`);

  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const accept = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await apiSend(`/v1/auth/invitations/${token}/accept`, {
        json: { name, password },
      });
      // Accepting signs them in — the response set the session cookie.
      router.push("/projects");
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status !== 0
          ? caught.detail
          : "could not reach the server"
      );
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-6">
      <Logo className="mb-8" />

      {invitation.loading && (
        <p className="text-sm text-muted-foreground">Checking this invitation…</p>
      )}

      {invitation.error && (
        <div className="space-y-3">
          <h1 className="text-xl font-semibold tracking-tight">
            This invitation is no longer valid
          </h1>
          {/* Expired, withdrawn, already used and never-existed are one answer
              at the API on purpose, so this cannot say which. */}
          <p className="text-sm text-muted-foreground">
            It may have expired, been withdrawn, or already been used. Ask
            whoever invited you to send another.
          </p>
          <Link href="/login" className="inline-block text-sm underline">
            Sign in instead
          </Link>
        </div>
      )}

      {invitation.data && (
        <form onSubmit={accept} className="space-y-5">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">
              Join {invitation.data.orgName}
            </h1>
            <p className="mt-1.5 text-sm text-muted-foreground">
              Invited as {invitation.data.role} ·{" "}
              <span dir="ltr">{invitation.data.email}</span>
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="name">Your name</Label>
            <Input
              id="name"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="How you appear to your team"
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="password">Choose a password</Label>
            <Input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {/* The API is the authority on what is strong enough and says so
                in its own words; repeating a rule here is a second copy that
                will disagree with it. */}
          </div>

          {error && (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          )}

          <Button type="submit" className="w-full" disabled={busy || !password}>
            {busy ? "Joining…" : `Join ${invitation.data.orgName}`}
          </Button>
        </form>
      )}
    </main>
  );
}
