import type { Metadata } from 'next';
import './globals.css';
import './checkout-studio.css';
export const metadata: Metadata = { title: 'Scanz — Payment help, resolved', description: 'Welcome to Scanz. Your checkout payment support teammate. Local demo with simulated payments.' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body>{children}</body></html>; }
