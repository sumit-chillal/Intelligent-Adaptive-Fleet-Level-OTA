import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import "./globals.css";

/**
 * Type pairing from docs/Design.md.
 *
 * Archivo carries a width axis, which is what gives the display face its
 * expanded, silkscreened instrument-panel character. IBM Plex Sans and Mono
 * come from the same industrial vernacular — Plex Mono sets every device id,
 * version string and hash on the page, because those are identifiers and
 * monospacing is how the eye scans a column of them.
 */
// No `weight` here on purpose. next/font loads the STATIC cut of a family
// when a weight is listed, and axes can only be defined on a variable font --
// so naming weights and axes together is a contradiction and fails the build.
// Omitting weight loads the variable font; weights are then set in CSS.
const display = Archivo({
  subsets: ["latin"],
  axes: ["wdth"],
  variable: "--font-display",
  display: "swap",
});
const body = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-body",
  display: "swap",
});
const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "CONVOY — fleet OTA",
  description: "Adaptive fleet-level OTA firmware deployment",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}