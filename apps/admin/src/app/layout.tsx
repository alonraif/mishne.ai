import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "mishne.ai back-office",
  // Nothing here should ever be indexed, and nothing here should ever be
  // reachable by something that indexes. Both, since neither costs anything.
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
