import type { Metadata } from "next";
import Link from "next/link";
import { Archive, Sparkles } from "lucide-react";
import { ToastProvider } from "@/components/ui/toast";
import { SavantLogo } from "@/components/savant-logo";
import "./globals.css";

export const metadata: Metadata = {
  title: "Savantsdatabas — Savant Media",
  description: "Databas över kunders företag för Savant Media",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="sv">
      <body className="min-h-screen bg-gray-100 text-gray-800 antialiased">
        <ToastProvider>
          <header className="sticky top-0 z-50 bg-white border-b border-gray-200 shadow-sm">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
              <Link
                href="/"
                className="flex items-center gap-2.5 text-lg font-bold text-gray-900 hover:text-blue-700 transition-colors"
              >
                <SavantLogo size={32} />
                <span>Savantsdatabas</span>
              </Link>
              <nav className="flex items-center gap-1">
                <Link
                  href="/arkiv"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-600 hover:text-blue-700 hover:bg-blue-50 transition-colors"
                >
                  <Archive className="w-4 h-4" />
                  Arkiv
                </Link>
                <Link
                  href="/orchestrator"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-600 hover:text-violet-700 hover:bg-violet-50 transition-colors"
                >
                  <Sparkles className="w-4 h-4" />
                  Orchestrator
                </Link>
              </nav>
            </div>
          </header>
          <main>{children}</main>
        </ToastProvider>
      </body>
    </html>
  );
}
