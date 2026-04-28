import type { ReactNode } from "react";

import { SectionErrorBoundary } from "@/components/error-boundary";

/**
 * Phase 4.6: per-route ErrorBoundary для /dna/*. DNA-чанки самые
 * нагруженные (chromosome painting, big match lists), и наш приоритет —
 * чтобы их падение не валило весь tree-view. См. ADR-0041.
 */
export default function DnaLayout({ children }: { children: ReactNode }) {
  return <SectionErrorBoundary>{children}</SectionErrorBoundary>;
}
