"use client";

/**
 * Phase 11.1 — tree-picker dropdown в `<SiteHeader>`.
 *
 * Контракт:
 * - Если у пользователя ≥1 дерева (включая shared) — рисуем dropdown.
 * - Если 0 — не рендерим ничего (sign-of-empty-state, см. task spec §3).
 * - Внутри dropdown'а: список деревьев, текущее подсвечено, last-active первым,
 *   ниже — «Manage trees» → /dashboard.
 * - Click по дереву: записываем cookie `current_tree_id` + редирект на /trees/{id}.
 *
 * Backend `GET /users/me/trees` ещё не существует (Phase 4.13c). До тех пор
 * `fetchUserTrees()` возвращает [] и dropdown скрыт. Когда endpoint появится,
 * меняется только `lib/user-trees.ts` — этот компонент трогать не нужно.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  type UserTreeSummary,
  fetchUserTrees,
  readCurrentTreeId,
  writeCurrentTreeId,
} from "@/lib/user-trees";
import { cn } from "@/lib/utils";

export function TreePicker() {
  const t = useTranslations("sharing.treePicker");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [currentTreeId, setCurrentTreeId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Cookie читаем после mount'а — на server'е document.cookie недоступен,
  // и SSR-render всегда рисует currentTreeId=null (что для UI допустимо: ни
  // одного item не подсветится до hydration'а).
  useEffect(() => {
    setCurrentTreeId(readCurrentTreeId());
  }, []);

  const { data, isLoading } = useQuery({
    queryKey: ["sharing", "user-trees"],
    queryFn: fetchUserTrees,
    refetchOnWindowFocus: false,
  });

  // Outside-click закрывает dropdown.
  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const trees = sortTrees(data?.items ?? [], currentTreeId);

  // Phase 11.1 spec: 0 trees → не рендерим dropdown.
  if (!isLoading && trees.length === 0) {
    return null;
  }

  const currentTree = trees.find((tree) => tree.id === currentTreeId) ?? trees[0];

  const handleSelect = (treeId: string) => {
    writeCurrentTreeId(treeId);
    setCurrentTreeId(treeId);
    setOpen(false);
    router.push(`/trees/${treeId}`);
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("label")}
        onClick={() => setOpen((prev) => !prev)}
        data-testid="tree-picker-trigger"
        className={cn(
          "flex max-w-[14rem] items-center gap-2 rounded-md border border-[color:var(--color-border)]",
          "px-2 py-1 text-sm font-medium hover:bg-[color:var(--color-surface-2,rgba(15,23,42,0.04))]",
        )}
      >
        <span className="truncate">{currentTree?.name ?? t("loading")}</span>
        <span aria-hidden="true" className="text-xs">
          ▾
        </span>
      </button>

      {open ? (
        <div
          role="menu"
          aria-label={t("label")}
          className={cn(
            "absolute right-0 top-full z-50 mt-1 w-64 overflow-hidden rounded-md border",
            "border-[color:var(--color-border)] bg-[color:var(--color-surface)] shadow-lg",
          )}
        >
          <ul className="max-h-72 overflow-y-auto">
            {trees.map((tree) => {
              const isCurrent = tree.id === currentTreeId;
              return (
                <li key={tree.id}>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => handleSelect(tree.id)}
                    data-testid={`tree-picker-item-${tree.id}`}
                    aria-current={isCurrent ? "true" : undefined}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm",
                      "hover:bg-[color:var(--color-surface-2,rgba(15,23,42,0.04))]",
                      isCurrent &&
                        "bg-[color:var(--color-surface-2,rgba(15,23,42,0.06))] font-medium",
                    )}
                  >
                    <span className="truncate">{tree.name}</span>
                    {isCurrent ? (
                      <span className="text-xs text-[color:var(--color-ink-500)]">
                        {t("current")}
                      </span>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="border-t border-[color:var(--color-border)] p-1">
            <Button asChild variant="ghost" size="sm" className="w-full justify-start">
              <Link href="/dashboard" onClick={() => setOpen(false)}>
                {t("manage")}
              </Link>
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Сортировка trees: сперва last-active, потом alphabetically.
 * `currentTreeId` не вытаскивается «наверх» — current уже отображается
 * в trigger'е, нет смысла дублировать его first-position'ом.
 */
function sortTrees(
  trees: readonly UserTreeSummary[],
  _currentTreeId: string | null,
): UserTreeSummary[] {
  return [...trees].sort((a, b) => {
    const aActive = a.last_active_at ? Date.parse(a.last_active_at) : 0;
    const bActive = b.last_active_at ? Date.parse(b.last_active_at) : 0;
    if (aActive !== bActive) return bActive - aActive;
    return a.name.localeCompare(b.name);
  });
}
