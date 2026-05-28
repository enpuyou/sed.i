import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  /* config options here */
};

// Wrap with Sentry only when DSN is configured.
// When NEXT_PUBLIC_SENTRY_DSN is absent the withSentryConfig wrapper is a no-op
// so local dev and CI don't require a Sentry account.
export default process.env.NEXT_PUBLIC_SENTRY_DSN
  ? withSentryConfig(nextConfig, {
      silent: true,
      // Skip source map upload until a Sentry auth token is configured
      sourcemaps: { disable: !process.env.SENTRY_AUTH_TOKEN },
    })
  : nextConfig;
