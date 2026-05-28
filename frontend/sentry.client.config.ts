import * as Sentry from "@sentry/nextjs";

// Only initialise when DSN is provided — safe to omit in local dev
const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NODE_ENV,
    // Capture 10% of sessions for replay (free-tier friendly)
    replaysSessionSampleRate: 0.1,
    replaysOnErrorSampleRate: 1.0,
    integrations: [Sentry.replayIntegration()],
    sendDefaultPii: false,
  });
}
