import type { Metadata } from "next";
import "./globals.css";
import "./xray.css";
export const metadata: Metadata = { title: "BuilderXray — public record checks", description: "A cited review of public builder records.", icons: { icon: "/icon.svg", shortcut: "/icon.svg" } };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body suppressHydrationWarning>{children}</body></html>; }
