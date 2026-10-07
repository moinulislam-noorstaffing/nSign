import type { Metadata } from 'next';
import './globals.css';
import { Providers } from './providers';
import { Nav } from '@/components/nav';
import { themeScript } from '@/components/theme';

export const metadata: Metadata = {
  title: 'Offer Letter Studio',
  description: 'Document templates, branch coverage and e-signature for HELIX',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: themeScript }} /></head>
      <body className="min-h-screen antialiased">
        <Providers>
          <div className="grid min-h-screen grid-cols-[248px_1fr] max-lg:grid-cols-1">
            <Nav />
            <main className="min-w-0">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
