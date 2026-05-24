import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {};

export default withSentryConfig(nextConfig, {
  silent: true,
  org: undefined,
  project: undefined,
  disableLogger: true,
  automaticVercelMonitors: false,
});
