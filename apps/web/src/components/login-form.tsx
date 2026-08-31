"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";

/**
 * Sign in.
 *
 * The error is whatever the API said and nothing more specific: "no such
 * account" and "wrong password" are deliberately the same answer there, and
 * helpfully distinguishing them in the UI would undo that.
 */
export function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/v1/auth/login", { method: "POST", json: { email, password } });
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

  const startSso = async () => {
    setError("");
    try {
      const { url } = await api<{ url: string }>("/v1/auth/sso/start");
      window.location.href = url;
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.detail : "single sign-on is unavailable"
      );
    }
  };

  return (
    <form className="space-y-4" onSubmit={submit}>
      <div className="grid gap-2">
        <Label htmlFor="email">Work email</Label>
        <Input
          id="email"
          type="email"
          autoComplete="username"
          placeholder="you@studio.tv"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <Button className="w-full" type="submit" disabled={busy}>
        {busy ? "Signing in…" : "Continue"}
      </Button>

      <div className="flex items-center gap-3">
        <Separator className="flex-1" />
        <span className="text-xs text-muted-foreground">or</span>
        <Separator className="flex-1" />
      </div>

      <Button
        type="button"
        variant="outline"
        className="w-full"
        onClick={() => void startSso()}
      >
        Continue with SSO
      </Button>

      {/* Access is by invitation (`Settings.public_signup`), so the honest
          answer to "no account?" is not a sign-up form. Pointing at one that
          answers 403 is a worse first impression than saying so. */}
      <p className="text-center text-sm text-muted-foreground">
        No account? Ask someone in your organisation to invite you.
      </p>
    </form>
  );
}
