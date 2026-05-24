import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "aifolimizer",
  description: "AI-powered portfolio advisor",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        suppressHydrationWarning
        className="font-sans bg-slate-950 text-white min-h-screen antialiased"
      >
        {children}
      </body>
    </html>
  );
}
