"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CustomNodeElementProps, RawNodeDatum } from "react-d3-tree";

import type { AncestorTreeNode } from "@/lib/api";
import { cn } from "@/lib/utils";

// react-d3-tree использует d3-zoom, который завязан на window/document —
// SSR-рендер падает. Грузим динамически, без SSR. ssr:false означает,
// что серверный рендер отдаст пустой контейнер, а Tree поднимется на клиенте.
const Tree = dynamic(() => import("react-d3-tree").then((mod) => mod.default), {
  ssr: false,
});

const NODE_WIDTH = 200;
const NODE_HEIGHT = 88;

/**
 * Конвертирует рекурсивный ``AncestorTreeNode`` (формат API) в
 * ``RawNodeDatum`` (формат react-d3-tree). Pedigree-семантика: дети узла —
 * это его родители (отец, мать). Корень слева, родители раскручиваются
 * вправо при ``orientation="horizontal"``.
 */
export function toRawNode(node: AncestorTreeNode): RawNodeDatum {
  const children: RawNodeDatum[] = [];
  if (node.father) children.push(toRawNode(node.father));
  if (node.mother) children.push(toRawNode(node.mother));

  return {
    name: node.primary_name ?? "Unnamed",
    attributes: {
      personId: node.id,
      sex: node.sex,
      birthYear: node.birth_year ?? "",
      deathYear: node.death_year ?? "",
      dnaTested: node.dna_tested ?? false,
    },
    children: children.length > 0 ? children : undefined,
  };
}

function sexIcon(sex: string): string {
  if (sex === "M") return "♂";
  if (sex === "F") return "♀";
  return "⚧";
}

function lifeYears(
  birth: string | number | boolean | undefined,
  death: string | number | boolean | undefined,
): string | null {
  const b = birth === "" || birth === undefined || birth === false ? null : String(birth);
  const d = death === "" || death === undefined || death === false ? null : String(death);
  if (!b && !d) return null;
  return `${b ?? "?"} – ${d ?? ""}`.trimEnd();
}

export function PedigreeTree({ root }: { root: AncestorTreeNode }) {
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);

  // Делаем translate начальной точки в левую часть контейнера, чтобы корень
  // (anchor person) был у левого края, а предки уходили вправо.
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const { width, height } = entry.contentRect;
      setDimensions({ width, height });
    });
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
    };
  }, []);

  const data = useMemo(() => toRawNode(root), [root]);
  const translate = dimensions ? { x: 60, y: dimensions.height / 2 } : { x: 60, y: 300 };

  const renderNode = ({ nodeDatum, hierarchyPointNode }: CustomNodeElementProps) => {
    const personId = String(nodeDatum.attributes?.personId ?? "");
    const sex = String(nodeDatum.attributes?.sex ?? "U");
    const years = lifeYears(nodeDatum.attributes?.birthYear, nodeDatum.attributes?.deathYear);
    const dnaTested = nodeDatum.attributes?.dnaTested === true;
    const isRoot = hierarchyPointNode.depth === 0;

    const handleNavigate = () => {
      if (personId) router.push(`/persons/${personId}/tree`);
    };

    return (
      <g>
        <foreignObject
          width={NODE_WIDTH}
          height={NODE_HEIGHT}
          x={-NODE_WIDTH / 2}
          y={-NODE_HEIGHT / 2}
        >
          <button
            type="button"
            onClick={handleNavigate}
            aria-label={`Open pedigree for ${nodeDatum.name}`}
            className={cn(
              "relative flex h-full w-full cursor-pointer flex-col items-start justify-center gap-0.5",
              "rounded-lg border bg-[color:var(--color-surface)] px-3 py-2 text-left",
              "shadow-sm transition-shadow",
              "hover:shadow-md hover:bg-[color:var(--color-surface-muted)]",
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)] focus-visible:ring-offset-2",
              isRoot
                ? "border-[color:var(--color-accent)] ring-1 ring-[color:var(--color-accent)]"
                : "border-[color:var(--color-border)]",
            )}
          >
            <span className="flex w-full items-center justify-between gap-2">
              <span className="truncate text-sm font-semibold leading-tight tracking-tight">
                {nodeDatum.name}
              </span>
              <span aria-hidden="true" className="text-base text-[color:var(--color-ink-500)]">
                {sexIcon(sex)}
              </span>
            </span>
            <span className="text-xs text-[color:var(--color-ink-500)]">
              {years ?? "dates unknown"}
            </span>
            {dnaTested ? (
              <span
                title="DNA tested"
                className={cn(
                  "absolute right-1.5 top-1.5 rounded-full px-1.5 py-0.5",
                  "bg-[color:var(--color-accent)] text-[10px] font-semibold leading-none text-white",
                )}
              >
                DNA
              </span>
            ) : null}
          </button>
        </foreignObject>
      </g>
    );
  };

  return (
    <div
      ref={containerRef}
      className="h-[70vh] w-full rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-muted)]"
    >
      {dimensions ? (
        <Tree
          data={data}
          orientation="horizontal"
          pathFunc="step"
          collapsible={false}
          zoomable
          translate={translate}
          nodeSize={{ x: NODE_WIDTH + 60, y: NODE_HEIGHT + 24 }}
          renderCustomNodeElement={renderNode}
          separation={{ siblings: 1, nonSiblings: 1.2 }}
        />
      ) : null}
    </div>
  );
}
