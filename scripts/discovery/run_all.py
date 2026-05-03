"""Phase 5.11a discovery orchestrator.

Запускает ``probe.py`` (sections A/B/D/E/F) и опционально ``probe_dupes.py``
(section C) на наборе GED-файлов. Каждый probe — отдельный subprocess с:

* hard-timeout (по умолчанию 120 секунд / 2 мин);
* внешним sampler'ом peak RSS (через psutil.Process(child).memory_info());
* захватом stdout/stderr/exit-code.

Файлы передаются через CLI или через env var ``GEDCOM_TEST_CORPUS``
(аналогично остальным скриптам репозитория). НЕТ HARDCODED PATHS.

Вывод — единый JSON-объект с per-file результатами + meta. Сохраняем
в указанный ``--out-dir`` (default: ``./_discovery_runs/<timestamp>/``,
gitignored через .gitignore-паттерн ``_discovery_runs/``). НЕ коммитим
никакой контент GED-файлов.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

# Force UTF-8 stdout — наша же проблема, что и в probes (Windows cp1252).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


HERE = Path(__file__).resolve().parent


def _format_bytes(n: int) -> str:
    if n is None:
        return "?"
    units = ["B", "KB", "MB", "GB"]
    i = 0
    f = float(n)
    while f >= 1024 and i < len(units) - 1:
        f /= 1024
        i += 1
    return f"{f:.2f}{units[i]}"


def _sample_rss(pid: int, peak: list[int], stop: threading.Event, interval: float = 0.1) -> None:
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    while not stop.is_set():
        try:
            rss = proc.memory_info().rss
            peak[0] = max(peak[0], rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        if stop.wait(interval):
            return


def _run_probe(
    probe_module: str,
    ged: Path,
    timeout: float,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """Запустить probe в subprocess'е; вернуть dict с stdout(JSON)/stderr/timing."""
    extra_args = list(extra_args or [])
    cmd = [
        sys.executable,
        "-X",
        "faulthandler",
        str(HERE / probe_module),
        str(ged),
        *extra_args,
    ]
    started_at = time.time()
    peak_rss = [0]
    stop_event = threading.Event()

    # PYTHONIOENCODING принуждает child печатать stdout в UTF-8 даже на
    # Windows (default = locale, обычно cp1252 → крах на emoji/кириллице).
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
    )
    sampler = threading.Thread(
        target=_sample_rss, args=(proc.pid, peak_rss, stop_event), daemon=True
    )
    sampler.start()

    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", "<communicate after kill timed out>"
    finally:
        stop_event.set()
        sampler.join(timeout=1.0)

    elapsed = round(time.time() - started_at, 3)
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    if stdout:
        # probe всегда печатает один JSON-объект. Берём последнюю не-пустую
        # строку (возможны тёплые UserWarning'и в stderr, но в stdout — только JSON).
        for raw_line in reversed(stdout.splitlines()):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
                break
            except json.JSONDecodeError as exc:
                parse_error = repr(exc)
                continue

    return {
        "probe": probe_module,
        "ged": str(ged),
        "timeout_secs": timeout,
        "elapsed_secs": elapsed,
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "external_peak_rss_bytes": peak_rss[0],
        "external_peak_rss_human": _format_bytes(peak_rss[0]),
        "stderr_tail": stderr.splitlines()[-15:] if stderr else [],
        "stdout_json_parse_error": parse_error,
        "result": parsed,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="GED-файлы (абсолютные пути). Если пусто — берём по env var GEDCOM_TEST_CORPUS.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="Папка с GED-файлами (override env var). Возьмёт все *.ged.",
    )
    parser.add_argument(
        "--corpus-glob",
        default="*.ged",
        help="Паттерн внутри corpus dir (default: *.ged)",
    )
    parser.add_argument(
        "--timeout-probe",
        type=float,
        default=120.0,
        help="Hard timeout для probe.py (default 120s)",
    )
    parser.add_argument(
        "--timeout-dupes",
        type=float,
        default=300.0,
        help="Hard timeout для probe_dupes.py (default 300s)",
    )
    parser.add_argument(
        "--dupes-on",
        nargs="*",
        default=[],
        help=(
            "Имена файлов (basename), на которых запустить probe_dupes.py. "
            "По дефолту — пусто (запускаем только если пользователь явно указал)."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("_discovery_runs"),
        help="Куда писать raw JSON outputs (gitignored).",
    )
    parser.add_argument(
        "--probe-args",
        nargs="*",
        default=[],
        help="Доп. аргументы пробрасываемые в probe.py (например --skip-validator).",
    )
    args = parser.parse_args(argv)

    files: list[Path] = list(args.files)
    if not files:
        corpus = args.corpus or (
            Path(os.environ["GEDCOM_TEST_CORPUS"]) if os.environ.get("GEDCOM_TEST_CORPUS") else None
        )
        if corpus is None:
            print(
                "ERROR: укажи файлы или GEDCOM_TEST_CORPUS / --corpus",
                file=sys.stderr,
            )
            return 2
        files = sorted(corpus.glob(args.corpus_glob))

    if not files:
        print("ERROR: нет файлов для probe", file=sys.stderr)
        return 2

    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir: Path = args.out_dir / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "timestamp": timestamp,
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
            "total_memory_bytes": psutil.virtual_memory().total,
        },
        "probe_args": args.probe_args,
        "files": [],
    }

    for ged in files:
        print(f"=== probe: {ged}  ({_format_bytes(ged.stat().st_size)}) ===", file=sys.stderr)
        per_file: dict[str, Any] = {"ged": str(ged), "size_bytes": ged.stat().st_size}
        per_file["probe_main"] = _run_probe(
            "probe.py",
            ged,
            args.timeout_probe,
            extra_args=args.probe_args,
        )
        if ged.name in args.dupes_on:
            print(f"--- dupes on {ged.name} ---", file=sys.stderr)
            per_file["probe_dupes"] = _run_probe(
                "probe_dupes.py",
                ged,
                args.timeout_dupes,
            )
        # Сохраняем per-file json (raw) — может понадобиться для отчёта.
        with (out_dir / f"{ged.stem}.json").open("w", encoding="utf-8") as fh:
            json.dump(per_file, fh, ensure_ascii=False, indent=2)
        summary["files"].append(per_file)

    summary_path = out_dir / "_summary.json"
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(f"\nSUMMARY: {summary_path}", file=sys.stderr)

    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
