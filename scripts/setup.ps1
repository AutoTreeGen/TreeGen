# =====================================================================
# AutoTreeGen — скрипт первичной настройки для Windows
# Запуск: .\scripts\setup.ps1
# =====================================================================

$ErrorActionPreference = "Stop"

Write-Host "=== AutoTreeGen Setup ===" -ForegroundColor Cyan

# 1. Проверка наличия uv
Write-Host "`n[1/5] Проверка uv..." -ForegroundColor Yellow
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv не найден. Устанавливаю..." -ForegroundColor Yellow
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    Write-Host "uv установлен. ВАЖНО: перезапустите PowerShell и запустите этот скрипт снова." -ForegroundColor Red
    exit 1
}
Write-Host "uv найден: $(uv --version)" -ForegroundColor Green

# 2. Проверка Docker
Write-Host "`n[2/5] Проверка Docker..." -ForegroundColor Yellow
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker не найден. Установите Docker Desktop с https://www.docker.com/products/docker-desktop/" -ForegroundColor Red
    exit 1
}
Write-Host "Docker найден: $(docker --version)" -ForegroundColor Green

# 3. Создание .env из .env.example
Write-Host "`n[3/5] Создание .env..." -ForegroundColor Yellow
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host ".env создан. Отредактируйте его при необходимости." -ForegroundColor Green
} else {
    Write-Host ".env уже существует, пропускаю." -ForegroundColor Green
}

# 4. Запуск Docker-инфраструктуры
Write-Host "`n[4/5] Запуск Docker-сервисов (postgres, redis, minio)..." -ForegroundColor Yellow
docker compose up -d
Write-Host "Ожидание готовности сервисов..." -ForegroundColor Yellow
Start-Sleep -Seconds 5
docker compose ps

# 5. Установка Python зависимостей и pre-commit
Write-Host "`n[5/5] Установка Python зависимостей..." -ForegroundColor Yellow
uv sync --all-extras
Write-Host "Установка pre-commit хуков..." -ForegroundColor Yellow
uv run pre-commit install

# Прогон тестов
Write-Host "`n=== Прогон тестов парсера ===" -ForegroundColor Cyan
uv run pytest packages/gedcom-parser -v

Write-Host "`n=== Setup завершён успешно! ===" -ForegroundColor Green
Write-Host "Дальше:" -ForegroundColor Cyan
Write-Host "  - Положите ваш .ged в packages/gedcom-parser/samples/my_tree.ged"
Write-Host "  - Запустите: uv run gedcom-tool tokenize packages/gedcom-parser/samples/my_tree.ged"
Write-Host "  - Прочитайте ROADMAP.md и CLAUDE.md перед началом работы с Claude Code"
