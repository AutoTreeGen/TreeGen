"""Phase 10.9a — arq job-функции parser-service.

Каждая job — отдельный модуль (один callable + helper'ы). Регистрация
в ``WorkerSettings.functions`` — :mod:`parser_service.worker`.

Конвенция: job-функция тонкая: открывает session, делегирует heavy lifting
``services/*_runner.py``-helper'у, commit'ит, возвращает sterile dict. На
любом исключении — runner сам пишет failed-status; здесь только commit
(для persist'а failure-state'а) или re-raise.
"""
