export type IngestIssueKind =
  | "blocked"
  | "unauthorized"
  | "source_access"
  | "network"
  | "partial"
  | "unknown";

export interface IngestIssue {
  kind: IngestIssueKind;
  severity: "error" | "warning";
  badge: string;
  titleFallback: string;
  readerMessage: string;
}

const matches = (text: string, patterns: string[]) =>
  patterns.some((pattern) => text.includes(pattern));

export function getIngestIssue(
  processingStatus: string,
  processingError?: string | null,
  _originalUrl?: string | null,
): IngestIssue | null {
  const errorText = (processingError || "").toLowerCase().trim();
  const isFailed = processingStatus === "failed";
  const isPartial = processingStatus === "completed" && errorText.length > 0;

  if (!isFailed && !isPartial) {
    return null;
  }

  // --- Hard failures ---
  if (isFailed) {
    const isBlocked = matches(errorText, [
      "403",
      "forbidden (403)",
      "http 403",
      "403 client error",
      "blocks bots",
      "blocked",
      "bot",
      "captcha",
      "cloudflare",
      "rate limit",
      "too many requests",
      "429",
    ]);

    const isUnauthorized = matches(errorText, [
      "401",
      "http 401",
      "401 client error",
      "unauthorized",
      "not authorized",
      "authorization",
      "authentication required",
      "login required",
      "access denied",
    ]);

    const isSourceAccessIssue =
      isUnauthorized ||
      isBlocked ||
      matches(errorText, [
        "httperror",
        "client error",
        "server error",
        "http ",
        "status code",
        "for url",
      ]);

    const isNetwork = matches(errorText, [
      "timed out",
      "timeout",
      "request error",
      "network",
      "connection",
      "dns",
      "temporary",
      "unreachable",
    ]);

    if (isUnauthorized) {
      return {
        kind: "unauthorized",
        severity: "warning",
        badge: "Source site requires authorization",
        titleFallback: "This source requires login or subscription",
        readerMessage:
          "The source site requires authorization. Open the original URL to continue.",
      };
    }

    if (isBlocked) {
      return {
        kind: "blocked",
        severity: "warning",
        badge: "Blocked by source site",
        titleFallback: "This source blocked automated access",
        readerMessage:
          "This source blocks automated access. Open the original URL to read it.",
      };
    }

    if (isSourceAccessIssue) {
      return {
        kind: "source_access",
        severity: "warning",
        badge: "Source site access issue",
        titleFallback: "The source site prevented automated access",
        readerMessage:
          "The source site prevented automated access. Open the original URL to continue.",
      };
    }

    if (isNetwork) {
      return {
        kind: "network",
        severity: "error",
        badge: "Source connection issue",
        titleFallback: "We couldn't reach this article right now",
        readerMessage:
          "We couldn't reach the source site right now. Try again in a moment.",
      };
    }

    return {
      kind: "unknown",
      severity: "error",
      badge: "Extraction failed",
      titleFallback: "We couldn't extract this article",
      readerMessage:
        "We couldn't extract this article. Open the original URL to continue reading.",
    };
  }

  // --- Partial extraction (completed but processing_error set by backend) ---
  return {
    kind: "partial",
    severity: "warning",
    badge: "Limited extraction",
    titleFallback: "Saved, but full text is limited",
    readerMessage:
      "Only part of this article could be extracted. Open the original URL to read the full text.",
  };
}
