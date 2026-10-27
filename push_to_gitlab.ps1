# PowerShell helper to push O3AI to GitLab Hackathon remote
$env:PATH = "$env:LOCALAPPDATA\Programs\MinGit\cmd;$env:PATH"
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "Pushing O3AI to GitLab Hackathon Repository..." -ForegroundColor Cyan
Write-Host "Remote: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/39682298/o3ai.git" -ForegroundColor Gray
Write-Host "===================================================" -ForegroundColor Cyan

git push -u hackathon main --force
