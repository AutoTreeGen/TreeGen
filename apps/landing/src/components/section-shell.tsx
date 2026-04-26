"use client";

import { cn } from "@/lib/utils";
import * as motion from "motion/react-client";

/**
 * Контейнер для повторяющихся секций. Reveal-on-scroll через motion + viewport.
 * Eyebrow + Title + Description + children.
 */
export function SectionShell({
  id,
  eyebrow,
  title,
  description,
  children,
  className,
}: {
  id?: string;
  eyebrow: string;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={cn("relative scroll-mt-24 py-24 sm:py-32", className)}>
      <div className="container mx-auto max-w-6xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          className="mx-auto max-w-3xl text-center"
        >
          <p
            className="text-sm font-semibold uppercase tracking-[0.18em]
              text-[var(--color-brand-600)]"
          >
            {eyebrow}
          </p>
          <h2
            className="mt-3 text-balance font-display text-4xl font-bold tracking-tight
              text-[var(--color-ink-900)] sm:text-5xl"
          >
            {title}
          </h2>
          {description && (
            <p className="mt-5 text-pretty text-lg text-[var(--color-ink-600)]">{description}</p>
          )}
        </motion.div>

        <div className="mt-16">{children}</div>
      </div>
    </section>
  );
}
