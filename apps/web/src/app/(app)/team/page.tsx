"use client";

/**
 * Who is in this organisation, and who has been asked.
 *
 * Membership is the whole of the access model — there are no per-project ACLs
 * — so this screen is the access-control surface of the product, and it is
 * written to read like one: what each role can do is on the page rather than
 * in documentation, because the person choosing a role is choosing it now.
 */

import { useState } from "react";
import { Mail, ShieldCheck, Trash2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { CardsSkeleton, QueryState } from "@/components/query-state";
import { useSession } from "@/components/session-provider";
import { ApiError, api } from "@/lib/api";
import { apiSend } from "@/lib/dto";
import { useApi } from "@/lib/use-api";
import type { Role } from "@mishne/shared";

interface Member {
  id: string;
  email: string;
  name: string;
  role: Role;
}

interface Invitation {
  id: string;
  email: string;
  role: Role;
  expiresAt: string;
  createdAt: string;
}

const ROLES: Array<{ id: Role; what: string }> = [
  { id: "owner", what: "Everything, including billing, members and retention." },
  { id: "member", what: "Uploads footage and runs jobs." },
  { id: "viewer", what: "Reads transcripts and downloads artifacts." },
];

export default function TeamPage() {
  const { session } = useSession();
  const isOwner = session.user.role === "owner";
  const members = useApi<Member[]>("/v1/org/members");
  // Who has been *offered* a way in is an owner's question, not a roster —
  // the API refuses it to anyone else, so this does not ask.
  const invitations = useApi<Invitation[]>(isOwner ? "/v1/org/invitations" : null);

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [sentTo, setSentTo] = useState("");

  const invite = async (event: React.FormEvent) => {
    event.preventDefault();
    setSending(true);
    setError("");
    setSentTo("");
    try {
      await apiSend("/v1/org/members/invite", { json: { email, role } });
      setSentTo(email);
      setEmail("");
      invitations.refetch();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setSending(false);
    }
  };

  const revoke = async (id: string) => {
    await api(`/v1/org/invitations/${id}`, { method: "DELETE" }).catch(() => {});
    invitations.refetch();
  };

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Team</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {session.org.name} · everyone here can see this organisation&apos;s
          footage.
        </p>
      </div>

      {isOwner && (
        <Card className="p-5">
          <form onSubmit={invite} className="space-y-4">
            <div className="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-end">
              <div className="grid gap-2">
                <Label htmlFor="email">Invite by email</Label>
                <Input
                  id="email"
                  type="email"
                  required
                  value={email}
                  placeholder="colleague@example.com"
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <Button type="submit" disabled={sending || !email}>
                <Mail /> {sending ? "Sending…" : "Send invitation"}
              </Button>
            </div>

            <div className="flex flex-wrap gap-2">
              {ROLES.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => setRole(r.id)}
                  className={
                    "rounded-md border px-3 py-2 text-left text-sm transition-colors " +
                    (role === r.id
                      ? "border-primary bg-primary/5"
                      : "border-border hover:bg-accent/40")
                  }
                >
                  <span className="font-medium capitalize">{r.id}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {r.what}
                  </span>
                </button>
              ))}
            </div>

            {error && (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            )}
            {sentTo && (
              <p className="text-sm text-stage-done">
                Invitation sent to {sentTo}. The link is good for seven days and
                can be used once.
              </p>
            )}
          </form>
        </Card>
      )}

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Members</h2>
        <QueryState query={members} missing="Nobody here." skeleton={<CardsSkeleton rows={2} />}>
          {(list) => (
            <div className="grid gap-2">
              {list.map((m) => (
                <Card key={m.id} className="flex items-center gap-4 p-4">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium">
                      {m.name || m.email}
                      {m.id === session.user.id && (
                        <span className="ml-2 text-xs text-muted-foreground">you</span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground" dir="ltr">
                      {m.email}
                    </div>
                  </div>
                  <Badge variant={m.role === "owner" ? "default" : "muted"}>
                    {m.role === "owner" && <ShieldCheck className="mr-1 size-3" />}
                    {m.role}
                  </Badge>
                </Card>
              ))}
            </div>
          )}
        </QueryState>
      </section>

      {isOwner && (invitations.data?.length ?? 0) > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-medium text-muted-foreground">
            Invited, not yet joined
          </h2>
          <div className="grid gap-2">
            {invitations.data!.map((i) => (
              <Card
                key={i.id}
                className="flex items-center gap-4 border-dashed p-4"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-sm" dir="ltr">{i.email}</div>
                  <div className="text-xs text-muted-foreground">
                    expires{" "}
                    {new Date(i.expiresAt).toLocaleDateString("en-GB", {
                      day: "2-digit", month: "short",
                    })}
                  </div>
                </div>
                <Badge variant="muted">{i.role}</Badge>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => revoke(i.id)}
                  title="Withdraw this invitation"
                >
                  <X /> Withdraw
                </Button>
              </Card>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
