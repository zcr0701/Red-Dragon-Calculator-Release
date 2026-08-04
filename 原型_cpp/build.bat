@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "GXX="
where g++ >nul 2>nul && set "GXX=g++"
if not defined GXX if exist "%LOCALAPPDATA%\w64devkit\w64devkit\bin\g++.exe" set "GXX=%LOCALAPPDATA%\w64devkit\w64devkit\bin\g++.exe"

if not exist build mkdir build

if defined GXX (
  for %%i in ("%GXX%") do set "GXXDIR=%%~dpi"
  set "PATH=!GXXDIR!;%PATH%"
  echo [build] using g++: %GXX%
  "%GXX%" -std=c++17 -O2 -Wall -Wextra src\cards.cpp src\engine.cpp src\search.cpp src\format.cpp src\main.cpp -I src -o build\red_dragon_calc.exe
  if errorlevel 1 (
    echo [ERROR] compile failed
    exit /b 1
  )
  "%GXX%" -std=c++17 -O2 -Wall -Wextra src\cards.cpp src\engine.cpp src\search.cpp src\format.cpp src\webui.cpp -I src -lws2_32 -o build\red_dragon_webui.exe
  if errorlevel 1 (
    echo [ERROR] webui compile failed
    exit /b 1
  )
  echo [OK] build\red_dragon_calc.exe
  echo [OK] build\red_dragon_webui.exe
  exit /b 0
)

rem Fallback: MSVC (requires Visual Studio C++ workload and Windows SDK)
set "VCVARS="
if exist "D:\Microsoft\VisualStudio\Products\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=D:\Microsoft\VisualStudio\Products\VC\Auxiliary\Build\vcvars64.bat"
if not defined VCVARS for /f "usebackq delims=" %%i in (`"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -property installationPath`) do if exist "%%i\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%%i\VC\Auxiliary\Build\vcvars64.bat"
if not defined VCVARS (
  echo [ERROR] no C++ compiler found. Install MinGW-w64 or Visual Studio C++ workload.
  exit /b 1
)
call "%VCVARS%" >nul
cl /nologo /std:c++17 /O2 /EHsc /utf-8 /W3 src\cards.cpp src\engine.cpp src\search.cpp src\format.cpp src\main.cpp /I src /Fe:build\red_dragon_calc.exe /Fo:build\
if errorlevel 1 (
  echo [ERROR] MSVC compile failed - check Windows SDK installation.
  exit /b 1
)
echo [OK] build\red_dragon_calc.exe
exit /b 0
