import type { Metadata } from "next";
import { Playfair_Display } from "next/font/google";
import "./globals.css";

const playfair = Playfair_Display({
  subsets: ["latin"],
  variable: "--font-serif",
});

export const metadata: Metadata = {
  title: "Bungalo Control",
  description: "Monitor sync status, services, and outstanding tasks.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${playfair.variable} bg-background text-foreground`}>
        <div className="min-h-screen bg-gradient-to-b from-white via-background to-muted/60">
          {children}
        </div>
      </body>
    </html>
  );
}
