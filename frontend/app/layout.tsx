import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Resolve — Scan to resolution', description: 'A payment support teammate. Local hackathon demo with simulated payments.' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body>{children}</body></html>; }
