@echo off
chcp 65001 >nul
echo [build_dist] 正在打包红龙贼计算器（分发副本）...
"D:\Anaconda\python.exe" "%~dp0build_dist.py"
if %errorlevel%==0 (
    echo [build_dist] 完成：dist\红龙贼计算器\
) else (
    echo [build_dist] 失败
)
pause
