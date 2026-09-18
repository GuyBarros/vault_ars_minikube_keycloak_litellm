import type { Metadata } from 'next';
import { cookies } from 'next/headers';
import { IBM_Plex_Sans, IBM_Plex_Mono } from 'next/font/google';
import { ThemeProvider, type Theme } from '@/components/theme-provider';
import { THEME_COOKIE, getThemeCookie } from '@/lib/auth/session';
import './globals.scss';

const plexSans = IBM_Plex_Sans({
  weight: ['300', '400', '600'],
  subsets: ['latin'],
  variable: '--font-plex-sans',
  display: 'swap',
});

const plexMono = IBM_Plex_Mono({
  weight: ['400', '600'],
  subsets: ['latin'],
  variable: '--font-plex-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'AI Runtime Security',
  description: 'Governed conversations for your AI runtime, powered by Keycloak.',
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const theme: Theme = getThemeCookie(cookieStore.get(THEME_COOKIE)?.value);

  return (
    <html
      lang="en"
      data-carbon-theme={theme}
      className={`${plexSans.variable} ${plexMono.variable}`}
      suppressHydrationWarning
    >
      <body
        style={{
          fontFamily: `var(--font-plex-sans), 'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif`,
        }}
      >
        <ThemeProvider initialTheme={theme}>{children}</ThemeProvider>
      </body>
    </html>
  );
}
