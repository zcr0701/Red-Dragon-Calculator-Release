@echo off
chcp 65001 >nul
setlocal
set SRC=%~dp0red_dragon_core.cpp
set OUT=%~dp0red_dragon_calculator.exe

if exist "D:\mingw64\bin\g++.exe" (
  echo [build] use MinGW g++: D:\mingw64\bin\g++.exe
  "D:\mingw64\bin\g++.exe" -std=c++17 -O3 -static -o "%OUT%" "%SRC%"
  if %errorlevel%==0 (
    echo [build] done: %OUT%
    exit /b 0
  )
)

where g++ >nul 2>nul
if %errorlevel%==0 (
  echo [build] use g++ from PATH
  g++ -std=c++17 -O3 -static -o "%OUT%" "%SRC%"
  if %errorlevel%==0 (
    echo [build] done: %OUT%
    exit /b 0
  )
)

echo [build] FAILED: no usable C++ compiler found (need MinGW g++).
exit /b 1
