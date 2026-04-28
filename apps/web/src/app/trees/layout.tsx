import type { ReactNode } from "react";

import { SectionErrorBoundary } from "@/components/error-boundary";

/**
 * Phase 4.6: per-route ErrorBoundary для /trees/*. Падение pedigree-tree
 * рендеринга (D3 layout, big-tree memory blow) изолируется в этом
 * boundary; остальное приложение (header, /persons, /dna, etc) продолжает
 * работать. См. ADR-0041 §«Per-route boundaries».
 */
export default function TreesLayout({ children }: { children: ReactNode }) {
  return <SectionErrorBoundary>{children}</SectionErrorBoundary>;
}
