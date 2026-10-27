@echo off
set "PATH=%LOCALAPPDATA%\Programs\MinGit\cmd;%PATH%"
echo ===================================================
echo Pushing O3AI to GitLab Hackathon Repository...
echo ===================================================
git push -u hackathon main --force
echo.
pause
