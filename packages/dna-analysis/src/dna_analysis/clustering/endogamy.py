"""Endogamy detection (Phase 6.7a / ADR-0063).

Endogamy = генетическое наследование от ограниченного founder-пула, при
котором у людей в популяции много **дальних** общих предков. Эффект на
DNA-данные: пары делят повышенную avg cM **через много коротких
сегментов** (вместо одного-двух длинных), что путает обычный clustering
и triangulation алгоритм без поправки.

Heuristic per Phase 6.7 brief:

* высокая средняя pairwise cM в кластере → кандидат на endogamy;
* много отдельных IBD-сегментов на match (если caller это знает —
  опционально) → подтверждение;
* threshold выбирается **по самой эндогамной популяции** из known list.
  Если средний cM кластера превышает порог одной из них, метим это как
  population_label.

Population thresholds (per Phase 6.7 brief, AJ literature):

* **AJ (Ashkenazi Jewish):** ~30 cM avg pairwise — самый сильный effect,
  founder population ~350 для большинства Ashkenazi (Carmi et al.,
  «Sequencing an Ashkenazi reference panel supports population-targeted
  personal genomics and illuminates Jewish and European origins», Nat
  Commun 2014).
* **Mennonite (Old Order):** ~25 cM — closed-community endogamy, см.
  «Mennonite Genealogy Project» (PMC, 2014, шорт-сегменты в Pennsylvania
  Mennonite reference panel).
* **Iberian-Sephardic:** ~20 cM — partial endogamy, см. «Sephardic
  Jewish ancestry component in Iberian populations» (Nat Genet 2008).

Эти числа — **эвристика для выбора population_label**, не строгая
статистика. Reference-panel-based detection — Phase 6.5+ (см.
ROADMAP § Фаза 6.5).
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dna_analysis.clustering.graph import ClusterEdge, ClusterMatch

DEFAULT_ENDOGAMY_CM_THRESHOLD: Final[float] = 20.0
"""Минимальный средний pairwise cM кластера для endogamy-флага.

Совпадает с порогом наименее эндогамной из known populations
(Iberian-Sephardic). Кластеры со средним < этого значения — обычные
шорт-IBD без endogamy-pattern'а.
"""

DEFAULT_MIN_PAIRWISE_FOR_ENDOGAMY: Final[int] = 3
"""Минимум попарных рёбер в кластере для устойчивого решения.

На < 3 рёбрах среднее слишком шумно — отдаём warning=False по умолчанию,
даже если случайная пара перевалила threshold.
"""

# Population thresholds в cM, отсортированы по убыванию (нужно для
# выбора **самого специфичного** label'а: если кластер > AJ-порога,
# это AJ; иначе если > Mennonite — Mennonite; иначе Iberian-Sephardic).
POPULATION_THRESHOLDS_CM: Final[
    tuple[tuple[Literal["AJ", "mennonite", "iberian_sephardic"], float], ...]
] = (
    ("AJ", 30.0),
    ("mennonite", 25.0),
    ("iberian_sephardic", 20.0),
)


PopulationLabel = Literal["AJ", "mennonite", "iberian_sephardic"]


class EndogamyAssessment(BaseModel):
    """Результат detect_endogamy для одного кластера."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endogamy_warning: bool
    population_label: PopulationLabel | None = None
    avg_pairwise_cm: float = Field(default=0.0, ge=0.0)
    pair_count: int = Field(default=0, ge=0)
    avg_segments_per_member: float | None = Field(default=None, ge=0.0)


def _classify_population(avg_cm: float) -> PopulationLabel | None:
    """Map avg cM → population label, picking the most specific (highest threshold)."""
    for label, threshold in POPULATION_THRESHOLDS_CM:
        if avg_cm >= threshold:
            return label
    return None


def detect_endogamy(
    cluster_members: list[ClusterMatch],
    edges: list[ClusterEdge],
    *,
    cm_threshold: float = DEFAULT_ENDOGAMY_CM_THRESHOLD,
    min_pair_count: int = DEFAULT_MIN_PAIRWISE_FOR_ENDOGAMY,
) -> EndogamyAssessment:
    """Heuristic endogamy detection over одного кластера.

    Args:
        cluster_members: ClusterMatch объекты, входящие в этот кластер.
            Используется только для ``segments_with_owner`` сигнала
            (если caller его положил); membership-граф читается из ``edges``.
        edges: Все рёбра графа со всех кластеров. Функция сама фильтрует
            те, у которых оба конца — внутри ``cluster_members``.
        cm_threshold: Минимальный средний pairwise cM для endogamy-флага.
            См. :data:`DEFAULT_ENDOGAMY_CM_THRESHOLD`.
        min_pair_count: Минимум рёбер в кластере для устойчивого
            решения. На малых кластерах endogamy-warning консервативно
            False (избегаем флагать 2-человечный кластер только потому,
            что одна пара случайно тяжёлая).

    Returns:
        :class:`EndogamyAssessment` с warning-флагом, optional
        population_label, и debugging-полями (avg cM, pair count,
        avg segments per member).
    """
    if not cluster_members:
        return EndogamyAssessment(endogamy_warning=False)

    member_ids = {m.match_id for m in cluster_members}
    inside = [e for e in edges if e.source in member_ids and e.target in member_ids]
    pair_count = len(inside)
    if pair_count == 0:
        return EndogamyAssessment(endogamy_warning=False)

    # Игнорируем binary-fallback рёбра (weight=1.0 без cM-значения) при
    # вычислении среднего cM — они смещают среднее вниз и фейлят detect'.
    cm_values = [e.weight for e in inside if e.weight > 1.0]
    if not cm_values:
        # Все рёбра — binary fallback (Ancestry-style без числовых cM).
        # endogamy detection в этом режиме невозможен; warning=False
        # honestly, ADR это документирует как degraded mode.
        return EndogamyAssessment(
            endogamy_warning=False,
            avg_pairwise_cm=0.0,
            pair_count=pair_count,
        )
    avg_cm = sum(cm_values) / len(cm_values)

    seg_counts = [
        m.segments_with_owner for m in cluster_members if m.segments_with_owner is not None
    ]
    avg_segments_per_member: float | None = (
        sum(seg_counts) / len(seg_counts) if seg_counts else None
    )

    # Two-prong heuristic:
    #   1) avg_cm >= threshold (mandatory)
    #   2) pair_count >= min_pair_count (стабильность)
    # Сегменты — слабый «соусный» сигнал, добавляющий уверенность но не
    # обязательный (caller может не предоставить segments_with_owner).
    warning = avg_cm >= cm_threshold and pair_count >= min_pair_count

    return EndogamyAssessment(
        endogamy_warning=warning,
        population_label=_classify_population(avg_cm) if warning else None,
        avg_pairwise_cm=avg_cm,
        pair_count=pair_count,
        avg_segments_per_member=avg_segments_per_member,
    )
