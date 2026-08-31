"use client";

/**
 * The handful of primitives these screens need. Not a design system, and not
 * imported from the product's — see globals.css for why the back-office looks
 * like a different application on purpose.
 */

import { useEffect, useState } from "react";

export function Panel({
  title,
  right,
  children,
}: {
  title?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      className="rounded-lg border"
      style={{ borderColor: "var(--line)", background: "var(--panel)" }}
    >
      {(title || right) && (
        <header
          className="flex items-center justify-between border-b px-4 py-2.5"
          style={{ borderColor: "var(--line)" }}
        >
          <h2 className="text-sm font-medium">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs" style={{ color: "var(--muted)" }}>
        {label}
      </span>
      {children}
      {hint && (
        <span className="block text-xs" style={{ color: "var(--muted)" }}>
          {hint}
        </span>
      )}
    </label>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded border px-2.5 py-1.5 text-sm outline-none focus:border-[var(--accent)] ${props.className ?? ""}`}
      style={{
        borderColor: "var(--line)",
        background: "var(--panel-2)",
        color: "var(--text)",
        ...props.style,
      }}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className="w-full rounded border px-2.5 py-1.5 text-sm outline-none"
      style={{
        borderColor: "var(--line)",
        background: "var(--panel-2)",
        color: "var(--text)",
      }}
    />
  );
}

export function Button({
  tone = "normal",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: "normal" | "accent" | "danger";
}) {
  const background =
    tone === "accent" ? "var(--accent)" : tone === "danger" ? "var(--danger)" : "var(--panel-2)";
  const color = tone === "normal" ? "var(--text)" : "#101216";
  return (
    <button
      {...props}
      className={`rounded border px-3 py-1.5 text-sm font-medium disabled:opacity-40 ${props.className ?? ""}`}
      style={{ borderColor: "var(--line)", background, color, ...props.style }}
    />
  );
}

export function Note({ kind, children }: { kind: "error" | "ok"; children: React.ReactNode }) {
  if (!children) return null;
  return (
    <p
      className="rounded border px-3 py-2 text-sm"
      style={{
        borderColor: kind === "error" ? "var(--danger)" : "var(--ok)",
        color: kind === "error" ? "var(--danger)" : "var(--ok)",
      }}
    >
      {children}
    </p>
  );
}

export function Credits({ value }: { value: number }) {
  return <span className="tabular">{value.toFixed(2)}</span>;
}

export function When({ iso }: { iso: string | null }) {
  // Rendered after mount. Formatting a date on the server and again in the
  // browser produces two different strings in two different timezones, which
  // React reports as a hydration error on every row of every table.
  const [text, setText] = useState("");
  useEffect(() => {
    setText(iso ? new Date(iso).toLocaleString() : "—");
  }, [iso]);
  return <span className="tabular" suppressHydrationWarning>{text || "…"}</span>;
}
