import type { Metadata } from "next";
import "./globals.css";

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
      <body>{children}</body>
    </html>
  );
}
