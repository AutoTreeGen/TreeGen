# sanity_check.ps1
# Phase 2 verification: row counts in postgres + recursive ancestor query benchmark.
# Requires: docker compose up; alembic upgrade head; import_personal_ged.py done.

$ErrorActionPreference = "Stop"

$tables = @("persons", "names", "families", "family_children", "events", "event_participants", "audit_log", "places", "sources")

Write-Host ""
Write-Host "=== Row counts ===" -ForegroundColor Cyan
foreach ($t in $tables) {
    $sql = "SELECT '$t' AS tbl, count(*) AS rows FROM $t;"
    docker exec autotreegen-postgres psql -U autotreegen -d autotreegen -t -c $sql
}

Write-Host ""
Write-Host "=== Pick a root person UUID ===" -ForegroundColor Cyan
$rootSql = "SELECT id FROM persons LIMIT 1;"
$rootRaw = docker exec autotreegen-postgres psql -U autotreegen -d autotreegen -t -c $rootSql
# psql -t may emit array of strings; join then regex-extract pure UUID
$rootJoined = ($rootRaw -join " ")
$uuidPattern = [regex]'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
$rootMatch = $uuidPattern.Match($rootJoined)
if (-not $rootMatch.Success) {
    Write-Host "Could not parse UUID from psql output: '$rootJoined'" -ForegroundColor Red
    exit 1
}
$root = $rootMatch.Value
Write-Host "Root person: $root"

Write-Host ""
Write-Host "=== Recursive ancestor query (10 generations) ===" -ForegroundColor Cyan
$benchSql = @"
EXPLAIN ANALYZE
WITH RECURSIVE ancestors(person_id, generation) AS (
  SELECT '$root'::uuid, 0
  UNION ALL
  SELECT p.parent_id, a.generation + 1
  FROM ancestors a
  JOIN family_children fc ON fc.child_person_id = a.person_id
  JOIN families f ON f.id = fc.family_id,
  LATERAL (VALUES (f.husband_id), (f.wife_id)) AS p(parent_id)
  WHERE p.parent_id IS NOT NULL AND a.generation < 10
)
SELECT count(*) AS ancestor_count, max(generation) AS max_gen FROM ancestors;
"@
docker exec autotreegen-postgres psql -U autotreegen -d autotreegen -c $benchSql

Write-Host ""
Write-Host "Done. Target: < 200 ms execution time (Phase 2 perf target)." -ForegroundColor Green
