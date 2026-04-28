/**
 * Chromosome painting (Phase 6.3 / ADR-0033).
 *
 * SVG-визуализация 22 autosomes + X с подсвеченными shared-сегментами.
 * Высота на хромосому фиксирована, ширина scaled под фактическую длину
 * хромосомы (GRCh37 reference; приближённые длины из NCBI assembly
 * GRCh37.p13 — см. константу ниже).
 *
 * Privacy guards (ADR-0014 §«Privacy guards»):
 *   - Принимает только agg-сегменты {chromosome, start_bp, end_bp, cm}.
 *   - НЕ принимает rsid, genotypes, allele info — даже если backend
 *     случайно положит — рендер их игнорирует.
 *
 * Pure-presentation: компонент детерминированный, без данных-стейта,
 * пригоден для snapshot-тестирования (см. tests/chromosome-painting.test.tsx).
 */

import type { DnaMatchSegment } from "@/lib/dna-api";

/** Длины хромосом GRCh37/hg19 в bp (NCBI assembly GRCh37.p13). */
const CHROMOSOME_LENGTHS_BP: Record<number | "X", number> = {
  1: 249_250_621,
  2: 243_199_373,
  3: 198_022_430,
  4: 191_154_276,
  5: 180_915_260,
  6: 171_115_067,
  7: 159_138_663,
  8: 146_364_022,
  9: 141_213_431,
  10: 135_534_747,
  11: 135_006_516,
  12: 133_851_895,
  13: 115_169_878,
  14: 107_349_540,
  15: 102_531_392,
  16: 90_354_753,
  17: 81_195_210,
  18: 78_077_248,
  19: 59_128_983,
  20: 63_025_520,
  21: 48_129_895,
  22: 51_304_566,
  X: 155_270_560,
};

const ROW_HEIGHT = 14;
const ROW_GAP = 4;
const LABEL_WIDTH = 28;
const TRACK_WIDTH = 520;
const SEGMENT_FILL = "#10b981"; // emerald-500
const TRACK_FILL = "#e5e7eb"; // gray-200
const TRACK_STROKE = "#9ca3af"; // gray-400

const MAX_BP = Math.max(...Object.values(CHROMOSOME_LENGTHS_BP));

const ALL_ROWS: Array<{ key: number | "X"; label: string }> = [
  ...Array.from({ length: 22 }, (_, i) => ({ key: i + 1, label: String(i + 1) })),
  { key: "X", label: "X" },
];

export type ChromosomePaintingProps = {
  segments: DnaMatchSegment[];
  /** Подпись для скринридеров; кастомный label > генерик. */
  ariaLabel?: string;
};

/**
 * Гистограмма-painting 22 autosomes + X.
 *
 * Каждый ряд — одна хромосома. Полная серая полоска — её длина
 * относительно longest хромосомы (chr1). Зелёные прямоугольники —
 * shared segments из match.
 */
export function ChromosomePainting({ segments, ariaLabel }: ChromosomePaintingProps) {
  const totalHeight = ALL_ROWS.length * (ROW_HEIGHT + ROW_GAP) - ROW_GAP;
  const totalWidth = LABEL_WIDTH + TRACK_WIDTH;

  // Группируем сегменты по хромосоме — в DOM рендерим в порядке ALL_ROWS,
  // не в порядке появления, чтобы snapshot был стабилен.
  const byChrom = new Map<number | "X", DnaMatchSegment[]>();
  for (const seg of segments) {
    const key = normalizeChromosomeKey(seg.chromosome);
    if (key === null) continue;
    const list = byChrom.get(key) ?? [];
    list.push(seg);
    byChrom.set(key, list);
  }

  return (
    <svg
      role="img"
      aria-label={ariaLabel ?? `Chromosome painting with ${segments.length} shared segments`}
      width={totalWidth}
      height={totalHeight}
      viewBox={`0 0 ${totalWidth} ${totalHeight}`}
      xmlns="http://www.w3.org/2000/svg"
      className="block max-w-full"
    >
      <title>{ariaLabel ?? `Shared DNA — ${segments.length} segments`}</title>
      {ALL_ROWS.map((row, idx) => {
        const y = idx * (ROW_HEIGHT + ROW_GAP);
        // ``noUncheckedIndexedAccess``: row.key всегда есть в нашем
        // hardcoded map'е (1..22 + "X"), но TS этого не знает.
        const length = CHROMOSOME_LENGTHS_BP[row.key] ?? MAX_BP;
        const trackLen = (length / MAX_BP) * TRACK_WIDTH;
        const rowSegments = byChrom.get(row.key) ?? [];
        return (
          <g key={String(row.key)} data-chromosome={String(row.key)}>
            <text
              x={LABEL_WIDTH - 6}
              y={y + ROW_HEIGHT / 2}
              dominantBaseline="middle"
              textAnchor="end"
              fontSize={10}
              fontFamily="ui-monospace, monospace"
              fill="#374151"
            >
              {row.label}
            </text>
            <rect
              x={LABEL_WIDTH}
              y={y}
              width={trackLen}
              height={ROW_HEIGHT}
              rx={3}
              ry={3}
              fill={TRACK_FILL}
              stroke={TRACK_STROKE}
              strokeWidth={0.5}
            />
            {rowSegments.map((seg, segIdx) => {
              const start = clamp01(seg.start_bp / length) * trackLen;
              const end = clamp01(seg.end_bp / length) * trackLen;
              // Сегмент шириной < 1px рендерим как минимум 2px, иначе невидим
              // на длинных хромосомах. Это не искажает данные — только UX.
              const w = Math.max(2, end - start);
              return (
                <rect
                  // Сегменты упорядочены и стабильны → idx как key безопасен.
                  // biome-ignore lint/suspicious/noArrayIndexKey: stable order
                  key={segIdx}
                  data-testid={`segment-chr${String(row.key)}-${segIdx}`}
                  x={LABEL_WIDTH + start}
                  y={y + 1}
                  width={w}
                  height={ROW_HEIGHT - 2}
                  rx={2}
                  ry={2}
                  fill={SEGMENT_FILL}
                  fillOpacity={0.85}
                >
                  <title>
                    chr{row.label}: {seg.start_bp.toLocaleString()}–{seg.end_bp.toLocaleString()} ·{" "}
                    {seg.cm.toFixed(1)} cM
                    {seg.num_snps !== null ? ` · ${seg.num_snps.toLocaleString()} SNPs` : ""}
                  </title>
                </rect>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}

function clamp01(value: number): number {
  if (Number.isNaN(value)) return 0;
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

/**
 * Normalise chromosome value to map key.
 *
 * Принимаем backend-int (1..22), а также строки "1", "X", "x" — на случай,
 * если provenance jsonb когда-нибудь начнёт хранить строкой. Y и MT
 * пропускаем (Phase 6.1 algorithm их не считает).
 */
function normalizeChromosomeKey(value: number | string): number | "X" | null {
  if (typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 22) {
    return value;
  }
  if (typeof value === "string") {
    const upper = value.toUpperCase();
    if (upper === "X") return "X";
    const parsed = Number.parseInt(value, 10);
    if (Number.isInteger(parsed) && parsed >= 1 && parsed <= 22) return parsed;
  }
  return null;
}
