import type { ReactNode } from "react";

import { SectionErrorBoundary } from "@/components/error-boundary";

/** Phase 4.6: per-route ErrorBoundary для /hypotheses/*. См. ADR-0041. */
export default function HypothesesLayout({ children }: { children: ReactNode }) {
  return <SectionErrorBoundary>{children}</SectionErrorBoundary>;
}
