import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "mishne.ai",
  description:
    "Raw footage to editable rough cut. AAF, FCPXML and EDL, with a transcript that shows what was used and why.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
