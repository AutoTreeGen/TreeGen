"""CLI: ``seed-data ingest``.

Idempotent batch-load reference seeds в БД. Two modes:

* ``--canonical-only``: только committed canonical seeds (всегда работает,
  не требует ``SEED_DATA_DIR``). Default mode.
* ``--all``: canonical + local-only seeds (требует ``--data-dir`` или
  ``SEED_DATA_DIR`` env var).

Database URL читается из ``DATABASE_URL`` env var (стандарт parser-service /
billing-service / report-service конфигурации). CLI открывает свой
async-session, не зависит от FastAPI lifespan'а.

Пример::

    uv run seed-data ingest --canonical-only
    SEED_DATA_DIR=F:/Projects/TreeGen/data/reference uv run seed-data ingest --all
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from seed_data.config import canonical_paths, local_paths, resolve_data_dir
from seed_data.ingest import (
    UpsertCounts,
    upsert_countries,
    upsert_fabrication_patterns,
    upsert_places,
    upsert_surnames,
    upsert_transliterations,
)
from seed_data.loaders import (
    load_countries,
    load_fabrication_patterns,
    load_places,
    load_surnames,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="AutoTreeGen reference seed ingestion (Phase 22.1b).",
)

_logger = logging.getLogger("seed_data.cli")


@app.command("ingest")
def ingest(
    *,
    canonical_only: Annotated[
        bool, typer.Option("--canonical-only", help="Skip local-only seeds.")
    ] = False,
    all_seeds: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Load canonical + local-only seeds (requires --data-dir or SEED_DATA_DIR).",
        ),
    ] = False,
    data_dir: Annotated[
        Path | None,
        typer.Option(
            "--data-dir",
            help="Override SEED_DATA_DIR for local-only seeds.",
        ),
    ] = None,
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            help="Async-DSN postgres; defaults to DATABASE_URL env var.",
        ),
    ] = None,
) -> None:
    """Run the ingest. Default mode is canonical-only."""
    if all_seeds and canonical_only:
        typer.echo("--canonical-only and --all are mutually exclusive.", err=True)
        raise typer.Exit(code=2)
    if not all_seeds:
        canonical_only = True
    dsn = database_url or os.environ.get("DATABASE_URL")
    if not dsn:
        typer.echo(
            "DATABASE_URL not set. Pass --database-url or export DATABASE_URL.",
            err=True,
        )
        raise typer.Exit(code=2)

    resolved_data_dir = resolve_data_dir(data_dir) if not canonical_only else None
    if not canonical_only and resolved_data_dir is None:
        typer.echo(
            "--all requested but neither --data-dir nor SEED_DATA_DIR is set.",
            err=True,
        )
        raise typer.Exit(code=2)

    counts = asyncio.run(
        _run_ingest(dsn=dsn, canonical_only=canonical_only, data_dir=resolved_data_dir)
    )
    for c in counts:
        typer.echo(f"  {c.table}: {c.total} rows upserted")
    typer.echo(f"Done. {len(counts)} table(s) populated.")


async def _run_ingest(
    *,
    dsn: str,
    canonical_only: bool,
    data_dir: Path | None,
) -> list[UpsertCounts]:
    """Async core: открыть session, загрузить files, upsert."""
    engine = create_async_engine(dsn)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    counts: list[UpsertCounts] = []
    try:
        async with sf() as session:
            # ---- Canonical (always) ----
            paths = canonical_paths()
            counts.append(await upsert_countries(session, load_countries(paths["country_v1"])))
            counts.append(
                await upsert_fabrication_patterns(
                    session, load_fabrication_patterns(paths["fabrication"])
                )
            )

            # ---- Local-only (если --all + data_dir есть) ----
            if not canonical_only and data_dir is not None:
                local = local_paths(data_dir)
                # v2 country batches.
                for batch_path in local.country_v2_batches:
                    if not batch_path.exists():
                        continue
                    counts.append(
                        await upsert_countries(
                            session,
                            load_countries(batch_path, v2_batch=batch_path.stem),
                        )
                    )
                # USSR extension (одиночный файл).
                if local.country_ussr_extension.exists():
                    counts.append(
                        await upsert_countries(
                            session,
                            load_countries(
                                local.country_ussr_extension,
                                v2_batch="former_ussr_extension",
                            ),
                        )
                    )
                # Surnames + transliteration (один файл, оба блока).
                if local.surname_clusters.exists():
                    clusters, translit = load_surnames(local.surname_clusters)
                    counts.append(await upsert_surnames(session, clusters))
                    counts.append(await upsert_transliterations(session, translit))
                # Places.
                if local.place_lookup.exists():
                    counts.append(await upsert_places(session, load_places(local.place_lookup)))

            await session.commit()
    finally:
        await engine.dispose()
    return counts


def main() -> None:
    """Entrypoint для ``python -m seed_data``."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
