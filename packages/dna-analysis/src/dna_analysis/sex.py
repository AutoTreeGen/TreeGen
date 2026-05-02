"""Эвристика инференса биологического пола из распарсенного DNA-теста.

Используется потребителями (Phase 16.2 dna-painter и далее), которым нужно
знать sex для специальной обработки X / Y хромосом — но не хочется завязывать
их на провайдер-специфичные header-поля.

Контракт: pure function от `DnaTest`. Никаких side effects, никакой записи
в логи raw values.
"""

from __future__ import annotations

from dna_analysis.models import Chromosome, DnaTest, Genotype, Sex


def infer_sex(test: DnaTest) -> Sex:
    """Определяет биологический пол по распределению Y-хромосомных SNP.

    Эвристика:
      - Хотя бы один Y-SNP с валидным (не-NN) genotype → MALE.
      - Y-SNP присутствуют, но все NN (no-call) → FEMALE
        (типично для женского теста, где платформа всё равно выгружает
        Y-rows для совместимости формата).
      - Y-SNP отсутствуют целиком → UNKNOWN
        (некоторые провайдеры/процессы не экспортируют Y вообще —
        мы не можем отличить такой кит от женского без дополнительного сигнала).

    Heuristic-only: для медицинских/юридических контекстов нужен
    прямой self-report от владельца теста, а не ДНК-вывод.
    """
    y_snps = [snp for snp in test.snps if snp.chromosome is Chromosome.Y]
    if not y_snps:
        return Sex.UNKNOWN
    if any(snp.genotype is not Genotype.NN for snp in y_snps):
        return Sex.MALE
    return Sex.FEMALE
