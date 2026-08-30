import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "STAY",
  description: "Eine persönliche Frage beantworten"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="de"><body>{children}</body></html>;
}
