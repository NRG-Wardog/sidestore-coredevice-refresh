param(
    [string]$RepoName = "sidestore-dynamic-tunnel-diag"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is not installed. Install it first or create an empty GitHub repo manually."
}

gh auth status

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root
try {
    if (-not (Test-Path ".git")) {
        git init
        git add .
        git commit -m "Add SideStore dynamic tunnel diagnostic builder"
    }

    $login = gh api user --jq .login
    $full = "$login/$RepoName"

    gh repo view $full *> $null
    $exists = ($LASTEXITCODE -eq 0)

    if (-not $exists) {
        gh repo create $full --private --source . --remote origin --push
    } else {
        $origin = git remote get-url origin 2>$null
        if (-not $origin) {
            git remote add origin "https://github.com/$full.git"
        }
        git branch -M main
        git push -u origin main
    }

    Write-Host ""
    Write-Host "Repository ready: https://github.com/$full"
    Write-Host "Now open GitHub -> Actions -> Build SideStore Dynamic-Tunnel Diagnostic -> Run workflow."
}
finally {
    Pop-Location
}
