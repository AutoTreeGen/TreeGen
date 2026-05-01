"use client";

/**
 * SetEgoPersonPicker — Phase 10.7a / ADR-0068 surface для self-anchor'а.
 *
 * Контракт:
 * - Owner-only mutator: PATCH /trees/{id}/owner-person.
 * - Non-owners (EDITOR/VIEWER) видят disabled picker с tooltip'ом
 *   через ``canEdit`` prop. Реальное permission-разрешение делает
 *   backend (403 на PATCH); UI лишь не показывает affordance.
 * - Без anchor'а — empty state объясняющий, зачем self-anchor нужен.
 * - С anchor'ом — current selection + кнопка "Change" / "Clear".
 *
 * Поиск персон — переиспользуем ``searchPersons()`` (Phase 4.4.1):
 * ILIKE по given/surname с server-side limit. Phonetic-режим не
 * включаем по умолчанию — для self-anchor имя обычно знают точно.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  type TreeOwnerPersonResponse,
  searchPersons,
  setTreeOwnerPerson,
} from "@/lib/api";

export type SetEgoPersonPickerProps = {
  treeId: string;
  /** Текущий ``owner_person_id`` дерева (null если не set'нут). */
  currentOwnerPersonId: string | null;
  /**
   * Текущий пользователь — owner этого дерева? Non-owner получает
   * read-only render с tooltip-объяснением.
   */
  canEdit: boolean;
  /**
   * Optional: callback'ом передаётся новый owner_person_id (или null
   * на clear). Родитель использует это, чтобы пересинхронизировать
   * другие виджеты (badge на person card etc.).
   */
  onChange?: (response: TreeOwnerPersonResponse) => void;
};

export function SetEgoPersonPicker({
  treeId,
  currentOwnerPersonId,
  canEdit,
  onChange,
}: SetEgoPersonPickerProps) {
  const t = useTranslations("trees.egoAnchor");
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Загружаем текущую anchor-person для отображения её primary_name'а.
  // Используем search с лимитом 1 и q='' нельзя — q обязательно
  // непустой. Зато fetchPerson по id даст nameSummary напрямую.
  const currentPerson = useQuery({
    queryKey: ["ego-anchor-person", currentOwnerPersonId],
    queryFn: async () => {
      if (!currentOwnerPersonId) return null;
      // Импорт лениво — fetchPerson ниже в файле уже доступен из api.ts.
      const { fetchPerson } = await import("@/lib/api");
      return fetchPerson(currentOwnerPersonId);
    },
    enabled: currentOwnerPersonId !== null,
    refetchOnWindowFocus: false,
  });

  // Поиск — debounce'a нет; Input стреляет per-keystroke, react-query
  // дедупит идентичные queryKey. Для V1 ОК, особенно учитывая server-side
  // ILIKE с GIN-индексами.
  const search = useQuery({
    queryKey: ["ego-anchor-search", treeId, searchQuery],
    queryFn: () => searchPersons(treeId, { q: searchQuery, limit: 20 }),
    enabled: editing && searchQuery.length >= 2,
    refetchOnWindowFocus: false,
  });

  const setAnchor = useMutation({
    mutationFn: (personId: string | null) => setTreeOwnerPerson(treeId, personId),
    onSuccess: (response) => {
      setError(null);
      setEditing(false);
      setSearchQuery("");
      void queryClient.invalidateQueries({ queryKey: ["ego-anchor-person"] });
      onChange?.(response);
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : t("saveFailed"));
    },
  });

  const currentName = currentPerson.data
    ? primaryName(currentPerson.data)
    : currentOwnerPersonId
      ? t("loadingPerson")
      : null;

  // Disabled-вариант для non-owner.
  if (!canEdit) {
    return (
      <section
        aria-labelledby="ego-anchor-heading"
        className="rounded border border-[color:var(--color-border)] p-4"
      >
        <h2 id="ego-anchor-heading" className="text-lg font-semibold">
          {t("title")}
        </h2>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">{t("nonOwnerHint")}</p>
        {currentName ? (
          <p className="mt-3 text-sm">
            <strong>{t("currentLabel")}</strong> {currentName}
          </p>
        ) : (
          <p className="mt-3 text-sm text-[color:var(--color-ink-500)]">{t("notAnchored")}</p>
        )}
      </section>
    );
  }

  return (
    <section
      aria-labelledby="ego-anchor-heading"
      className="rounded border border-[color:var(--color-border)] p-4"
    >
      <h2 id="ego-anchor-heading" className="text-lg font-semibold">
        {t("title")}
      </h2>
      <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">{t("description")}</p>

      {!editing ? (
        <div className="mt-3 space-y-2">
          {currentName ? (
            <>
              <p className="text-sm">
                <strong>{t("currentLabel")}</strong> {currentName}
              </p>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => setEditing(true)}
                >
                  {t("change")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setAnchor.mutate(null)}
                  disabled={setAnchor.isPending}
                >
                  {t("clear")}
                </Button>
              </div>
            </>
          ) : (
            <>
              <p className="text-sm text-[color:var(--color-ink-500)]">{t("emptyState")}</p>
              <Button type="button" variant="secondary" size="sm" onClick={() => setEditing(true)}>
                {t("pickPerson")}
              </Button>
            </>
          )}
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <Input
            type="text"
            placeholder={t("searchPlaceholder")}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label={t("searchLabel")}
            data-testid="ego-anchor-search-input"
          />
          {searchQuery.length < 2 ? (
            <p className="text-xs text-[color:var(--color-ink-500)]">{t("searchMinLength")}</p>
          ) : search.isLoading ? (
            <p className="text-xs text-[color:var(--color-ink-500)]">{t("searching")}</p>
          ) : search.isError ? (
            <p className="text-xs text-red-700" role="alert">
              {t("searchFailed")}
            </p>
          ) : search.data && search.data.items.length > 0 ? (
            <ul
              className="divide-y divide-[color:var(--color-border)] rounded border border-[color:var(--color-border)]"
              data-testid="ego-anchor-results"
            >
              {search.data.items.map((person) => (
                <li key={person.id}>
                  <button
                    type="button"
                    onClick={() => setAnchor.mutate(person.id)}
                    disabled={setAnchor.isPending}
                    data-testid={`ego-anchor-result-${person.id}`}
                    className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-[color:var(--color-surface-2,rgba(15,23,42,0.04))]"
                  >
                    <span className="truncate">{person.primary_name ?? t("unnamedPerson")}</span>
                    <span className="text-xs text-[color:var(--color-ink-500)]">{person.sex}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-[color:var(--color-ink-500)]">{t("noResults")}</p>
          )}

          {error ? (
            <p className="text-sm text-red-800" role="alert">
              {error}
            </p>
          ) : null}

          <div className="flex gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setEditing(false);
                setSearchQuery("");
                setError(null);
              }}
              disabled={setAnchor.isPending}
            >
              {t("cancel")}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function primaryName(person: {
  names: { given_name: string | null; surname: string | null }[];
}): string {
  const first = person.names[0];
  if (!first) return "";
  return [first.given_name, first.surname].filter(Boolean).join(" ").trim();
}
