Describe "next-chain-number" {
    BeforeAll {
        $script:scriptPath = "$PSScriptRoot\..\next-chain-number.ps1"
    }

    It "Returns valid alembic JSON" {
        $result = pwsh $script:scriptPath -Type alembic -Json | ConvertFrom-Json
        $result.NextNumber | Should -Match '^\d{4}$'
        $result.DownRevision | Should -Match '^\d{4}$'
        [int]$result.NextNumber | Should -BeGreaterThan ([int]$result.DownRevision)
    }

    It "Returns valid ADR JSON" {
        $result = pwsh $script:scriptPath -Type adr -Json | ConvertFrom-Json
        $result.NextNumber | Should -Match '^\d{4}$'
    }

    It "Rejects invalid type" {
        # ValidateSet failure в дочернем pwsh — native exit code != 0,
        # а не terminating exception, поэтому проверяем $LASTEXITCODE.
        pwsh $script:scriptPath -Type invalid 2>&1 | Out-Null
        $LASTEXITCODE | Should -Not -Be 0
    }

    It "Skips numbers already claimed in worktrees" {
        # Smoke-проверка против текущего состояния — убеждаемся, что
        # возвращённый номер не пересекается с уже занятыми.
        $result = pwsh $script:scriptPath -Type alembic -Json | ConvertFrom-Json
        $result.ClaimedInWorktrees | Should -Not -Contain $result.NextNumber
    }
}
