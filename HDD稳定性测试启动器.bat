@echo off
setlocal enabledelayedexpansion
title HDD稳定性测试启动器
chcp 936 >nul 2>&1
cd /d "%~dp0"

set "OFFLINE_DIR=%~dp0"
set "PY_SCRIPT=hdd_stability_test.py"

rem ===== 管理员检测 =====
net session >nul 2>&1
if %errorlevel%==0 (set "IS_ADMIN=1") else (set "IS_ADMIN=0")

rem ===== 系统位数 =====
if defined PROCESSOR_ARCHITEW6432 (
    set "ARCH=%PROCESSOR_ARCHITEW6432%"
) else (
    set "ARCH=%PROCESSOR_ARCHITECTURE%"
)
if /i "%ARCH%"=="AMD64" (set "BITS=64") else (set "BITS=32")

rem ===== 系统版本 (仅Win7 6.1 需要补丁) =====
set "OS_MAJOR=0"
set "OS_MINOR=0"
for /f "tokens=2 delims==" %%v in ('wmic os get version /value 2^>nul ^| find "="') do set "OS_FULL=%%v"
for /f "tokens=1,2 delims=." %%a in ("%OS_FULL%") do (
    set "OS_MAJOR=%%a"
    set "OS_MINOR=%%b"
)
set "IS_WIN7="
if "%OS_MAJOR%"=="6" if "%OS_MINOR%"=="1" set "IS_WIN7=1"

echo ============================================================
echo   移动硬盘稳定性测试 - 启动器
echo ============================================================
echo 系统版本: %OS_MAJOR%.%OS_MINOR%
echo 系统位数: %BITS% 位
echo 离线包目录: %OFFLINE_DIR%
echo 管理员权限: %IS_ADMIN%
echo.

rem ============================================================
rem 第1步: 补丁检测 (仅 Win7 需要)
rem ============================================================
echo [1/4] 检查系统补丁...
set "NEED_KB="
if "%IS_WIN7%"=="1" goto :win7_patch
echo   [系统] Windows %OS_MAJOR%.%OS_MINOR%, 系统自带 Universal C Runtime, 无需安装补丁。
goto :python_step

:win7_patch
echo   [系统] Windows 7, 检查补丁...
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\HotFix\KB2533623" /reg:%BITS% >nul 2>&1
if errorlevel 1 goto :kb1_missing
echo   [OK] KB2533623 已安装
goto :kb2_check

:kb1_missing
echo   [!] 缺少 KB2533623 (Python安装前置补丁)
set "NEED_KB=1"

:kb2_check
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\HotFix\KB3118401" /reg:%BITS% >nul 2>&1
if errorlevel 1 goto :kb2_missing
echo   [OK] KB3118401 已安装
goto :need_kb_check

:kb2_missing
echo   [!] 缺少 KB3118401 (Universal C Runtime)
set "NEED_KB=1"

:need_kb_check
if not defined NEED_KB goto :python_step
if "%IS_ADMIN%"=="1" goto :install_kb
echo.
echo 需要管理员权限安装系统补丁, 正在请求提升权限...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b 0

:install_kb
echo   正在安装系统补丁...
if "%BITS%"=="64" (set "KB_ARCH=x64") else (set "KB_ARCH=x86")
if not exist "%OFFLINE_DIR%Windows6.1-KB2533623-%KB_ARCH%.msu" (
    echo   [错误] 缺少离线补丁包: Windows6.1-KB2533623-%KB_ARCH%.msu
) else (
    echo   安装 KB2533623 ...
    wusa.exe "%OFFLINE_DIR%Windows6.1-KB2533623-%KB_ARCH%.msu" /quiet /norestart
    if errorlevel 1 (echo   [警告] KB2533623 安装可能未成功) else (echo   [OK] KB2533623 安装完成)
)
if not exist "%OFFLINE_DIR%Windows6.1-KB3118401-%KB_ARCH%.msu" (
    echo   [错误] 缺少离线补丁包: Windows6.1-KB3118401-%KB_ARCH%.msu
) else (
    echo   安装 KB3118401 ...
    wusa.exe "%OFFLINE_DIR%Windows6.1-KB3118401-%KB_ARCH%.msu" /quiet /norestart
    if errorlevel 1 (echo   [警告] KB3118401 安装可能未成功) else (echo   [OK] KB3118401 安装完成)
)
echo.
echo 补丁安装完成, 可能需要重启系统后才能生效。
echo.

:python_step
echo [2/4] 检查 Python 环境...
set "PYTHON_EXE="

rem 优先用 py launcher 检测
py -3 --version >nul 2>&1
if errorlevel 1 goto :py_no_launcher
for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%i"
goto :python_found

:py_no_launcher
if exist "C:\Python38\python.exe" set "PYTHON_EXE=C:\Python38\python.exe"
if exist "C:\Python38-32\python.exe" set "PYTHON_EXE=C:\Python38-32\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python38\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python38\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe"
if exist "%ProgramFiles%\Python38\python.exe" set "PYTHON_EXE=%ProgramFiles%\Python38\python.exe"
if exist "%ProgramFiles(x86)%\Python38\python.exe" set "PYTHON_EXE=%ProgramFiles(x86)%\Python38\python.exe"

:python_found
if not defined PYTHON_EXE goto :py_install
"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 goto :py_bad
echo   [OK] 找到 Python: %PYTHON_EXE%
goto :py_ready

:py_bad
echo   [警告] Python 无法运行: %PYTHON_EXE%
set "PYTHON_EXE="

:py_install
if defined PYTHON_EXE goto :py_ready
echo   [!] 未检测到 Python 3.8
if "%IS_ADMIN%"=="1" goto :py_install_kb
echo.
echo 需要管理员权限安装 Python, 正在请求提升权限...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b 0

:py_install_kb
set "PY_INSTALLER="
if "%BITS%"=="64" (
    if exist "%OFFLINE_DIR%python-3.8.10-amd64.exe" set "PY_INSTALLER=%OFFLINE_DIR%python-3.8.10-amd64.exe"
) else (
    if exist "%OFFLINE_DIR%python-3.8.10.exe" set "PY_INSTALLER=%OFFLINE_DIR%python-3.8.10.exe"
)
if not defined PY_INSTALLER (
    echo   [错误] 缺少 Python 离线安装包!
    pause
    exit /b 1
)
echo   正在安装 Python 3.8.10 (静默安装)...
"%PY_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1
if errorlevel 1 goto :py_install_fail
echo   [OK] Python 安装完成
if "%BITS%"=="64" (
    if exist "C:\Python38\python.exe" set "PYTHON_EXE=C:\Python38\python.exe"
) else (
    if exist "C:\Python38-32\python.exe" set "PYTHON_EXE=C:\Python38-32\python.exe"
)
if defined PYTHON_EXE goto :py_ready
echo   [错误] Python 安装后未找到可执行文件
pause
exit /b 1

:py_install_fail
echo   [错误] Python 安装失败 (错误码 %errorlevel%)
pause
exit /b 1

:py_ready
echo.
echo [3/4] 检测可移动磁盘...
set "SYS_DRIVE=%SystemDrive%"
set "SYS_DRIVE=%SYS_DRIVE:~0,1%"
echo   系统盘: %SYS_DRIVE%:

set "DRV_COUNT=0"
for /f "skip=1 tokens=1-4 delims=," %%a in ('wmic logicaldisk get deviceid^,drivetype^,volumename /format:csv 2^>nul') do (
    set "DISK_ID=%%b"
    set "DISK_TYPE=%%c"
    set "DISK_VOL=%%d"
    if not "!DISK_ID!"=="" (
        if not "!DISK_ID!"=="Node" (
            set "LETTER=!DISK_ID:~0,1!"
            if not "!LETTER!"=="%SYS_DRIVE%" (
                if "!DISK_TYPE!"=="2" (
                    set /a DRV_COUNT+=1
                    set "DRV_!DRV_COUNT!=!LETTER!"
                    echo   !DRV_COUNT!. !LETTER!:  [可移动磁盘] !DISK_VOL!
                ) else if "!DISK_TYPE!"=="3" (
                    set /a DRV_COUNT+=1
                    set "DRV_!DRV_COUNT!=!LETTER!"
                    echo   !DRV_COUNT!. !LETTER!:  [本地磁盘] !DISK_VOL!
                )
            )
        )
    )
)

if %DRV_COUNT%==0 goto :no_drive
echo.
echo ============================================================
echo   请选择要测试的磁盘:
echo   (提示: USB移动硬盘通常显示为"本地磁盘", U盘显示为"可移动磁盘")
echo ============================================================
set "CHOICE="
set /p "CHOICE=请输入磁盘编号 (1-%DRV_COUNT%): "
goto :choice_done

:no_drive
echo   [警告] 未检测到可移动磁盘或本地磁盘, 已排除系统盘 %SYS_DRIVE%
echo   请确认移动硬盘/U盘已连接并识别。
pause
exit /b 1

:choice_done

set /a VALID_CHOICE=0
for /l %%i in (1,1,%DRV_COUNT%) do (
    if "!CHOICE!"=="%%i" set "VALID_CHOICE=1"
)
if not "!VALID_CHOICE!"=="1" (
    echo   [错误] 无效的选择
    pause
    exit /b 1
)

set "TARGET_DRIVE=!DRV_%CHOICE%!"
echo   已选择磁盘: %TARGET_DRIVE%:
if not exist "%TARGET_DRIVE%:\" (
    echo   [错误] 磁盘 %TARGET_DRIVE%: 不可访问
    pause
    exit /b 1
)
echo.

rem ============================================================
rem 第4步: 设定轮次, 拷贝脚本, 运行
rem ============================================================
echo [4/4] 准备测试...
set "CYCLES="
set /p "CYCLES=请输入测试循环次数 (1-9999, 建议1-10): "
if not defined CYCLES set "CYCLES=1"
set /a CYCLES_NUM=%CYCLES% 2>nul
if %CYCLES_NUM% lss 1 set "CYCLES_NUM=1"
if %CYCLES_NUM% gtr 9999 set "CYCLES_NUM=9999"

if not exist "%OFFLINE_DIR%%PY_SCRIPT%" (
    echo   [错误] 缺少测试脚本 %PY_SCRIPT%
    pause
    exit /b 1
)
copy /y "%OFFLINE_DIR%%PY_SCRIPT%" "%TARGET_DRIVE%:\%PY_SCRIPT%" >nul
if %errorlevel% neq 0 (
    echo   [错误] 无法拷贝脚本到 %TARGET_DRIVE%:\
    pause
    exit /b 1
)
echo   [OK] 脚本已拷贝到 %TARGET_DRIVE%:\%PY_SCRIPT%

echo.
echo ============================================================
echo   开始测试!
echo   磁盘: %TARGET_DRIVE%:
echo   轮次: %CYCLES_NUM%
echo   测试过程中请勿拔出硬盘!
echo ============================================================
echo.

"%PYTHON_EXE%" "%TARGET_DRIVE%:\%PY_SCRIPT%" --cycles %CYCLES_NUM% --yes

echo.
echo ============================================================
echo   测试流程结束
echo ============================================================
pause
exit /b 0