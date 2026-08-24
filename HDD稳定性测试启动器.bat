@echo off
setlocal enabledelayedexpansion
title HDD稳定性测试启动器
chcp 936 >nul 2>&1
cd /d "%~dp0"

rem ============================================================
rem  移动硬盘稳定性测试 - 启动器 (Win7及以上)
rem  功能: 环境检测/安装 -> 枚举磁盘 -> 拷贝脚本 -> 设定轮次 -> 运行
rem  离线包目录: 本脚本所在目录
rem ============================================================

set "OFFLINE_DIR=%~dp0"
set "PY_SCRIPT=hdd_stability_test.py"

rem ---------- 检查是否管理员 ----------
net session >nul 2>&1
if %errorlevel%==0 (set "IS_ADMIN=1") else (set "IS_ADMIN=0")

rem ---------- 检测系统位数 ----------
if defined PROCESSOR_ARCHITEW6432 (
    set "ARCH=%PROCESSOR_ARCHITEW6432%"
) else (
    set "ARCH=%PROCESSOR_ARCHITECTURE%"
)
if /i "%ARCH%"=="AMD64" (set "BITS=64") else (set "BITS=32")

echo ============================================================
echo   移动硬盘稳定性测试 - 启动器
echo ============================================================
echo 系统位数: %BITS% 位
echo 离线包目录: %OFFLINE_DIR%
echo 管理员权限: %IS_ADMIN%
echo.

rem ============================================================
rem 第1步: 检查/安装系统补丁 (KB2533623, KB3118401)
rem ============================================================
echo [1/4] 检查系统补丁...
set "NEED_KB="

rem 检测补丁是否已安装 (查注册表)
set "KB1_OK="
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\HotFix\KB2533623" /reg:64 >nul 2>&1
if %errorlevel%==0 set "KB1_OK=1"
if not defined KB1_OK (
    echo   [!] 缺少 KB2533623 (Python安装前置补丁)
    set "NEED_KB=1"
) else (
    echo   [OK] KB2533623 已安装
)

set "KB2_OK="
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\HotFix\KB3118401" /reg:64 >nul 2>&1
if %errorlevel%==0 set "KB2_OK=1"
if not defined KB2_OK (
    echo   [!] 缺少 KB3118401 (Universal C Runtime)
    set "NEED_KB=1"
) else (
    echo   [OK] KB3118401 已安装
)

rem 需要安装补丁时提权
if defined NEED_KB (
    if "%IS_ADMIN%"=="0" (
        echo.
        echo 需要管理员权限安装系统补丁，正在请求提升权限...
        echo 请在UAC提示中选择"是"。
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
        exit /b 0
    )
    echo 正在安装系统补丁...
    if not exist "%OFFLINE_DIR%Windows6.1-KB2533623-%BITS%.msu" (
        echo   [错误] 缺少离线补丁包: Windows6.1-KB2533623-%BITS%.msu
    ) else (
        echo   安装 KB2533623 ...
        wusa.exe "%OFFLINE_DIR%Windows6.1-KB2533623-%BITS%.msu" /quiet /norestart
        if %errorlevel%==0 (echo   [OK] KB2533623 安装完成) else (echo   [警告] KB2533623 安装可能未成功, 继续尝试)
    )
    if not exist "%OFFLINE_DIR%Windows6.1-KB3118401-%BITS%.msu" (
        echo   [错误] 缺少离线补丁包: Windows6.1-KB3118401-%BITS%.msu
    ) else (
        echo   安装 KB3118401 ...
        wusa.exe "%OFFLINE_DIR%Windows6.1-KB3118401-%BITS%.msu" /quiet /norestart
        if %errorlevel%==0 (echo   [OK] KB3118401 安装完成) else (echo   [警告] KB3118401 安装可能未成功, 继续尝试)
    )
    echo.
    echo 补丁安装完成，可能需要重启系统后才能生效。
    echo 建议: 如果 Python 安装仍报 api-ms-win-crt 相关错误，请重启系统后再运行本程序。
    echo.
)

rem ============================================================
rem 第2步: 检查/安装 Python 3.8
rem ============================================================
echo [2/4] 检查 Python 环境...
set "PYTHON_EXE="

rem 尝试 PATH 中的 python
for /f "delims=" %%i in ('where python 2^>nul') do (
    set "PYTHON_EXE=%%i"
    goto :python_found
)

rem 尝试常见安装路径
if exist "C:\Python38\python.exe" set "PYTHON_EXE=C:\Python38\python.exe"
if exist "C:\Python38-32\python.exe" set "PYTHON_EXE=C:\Python38-32\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python38\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python38\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe"

:python_found
if defined PYTHON_EXE (
    "%PYTHON_EXE%" --version >nul 2>&1
    if %errorlevel%==0 (
        echo   [OK] 找到 Python: %PYTHON_EXE%
    ) else (
        set "PYTHON_EXE="
    )
)

if not defined PYTHON_EXE (
    echo   [!] 未检测到 Python 3.8
    if "%IS_ADMIN%"=="0" (
        echo.
        echo 需要管理员权限安装 Python，正在请求提升权限...
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
        exit /b 0
    )
    set "PY_INSTALLER="
    if "%BITS%"=="64" (
        if exist "%OFFLINE_DIR%python-3.8.10-amd64.exe" set "PY_INSTALLER=%OFFLINE_DIR%python-3.8.10-amd64.exe"
    ) else (
        if exist "%OFFLINE_DIR%python-3.8.10.exe" set "PY_INSTALLER=%OFFLINE_DIR%python-3.8.10.exe"
    )
    if not defined PY_INSTALLER (
        echo   [错误] 缺少 Python 离线安装包! 请将 python-3.8.10 放入 %OFFLINE_DIR%
        pause
        exit /b 1
    )
    echo   正在安装 Python 3.8.10 (静默安装)...
    "%PY_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1
    if %errorlevel%==0 (
        echo   [OK] Python 安装完成
        if "%BITS%"=="64" (
            if exist "C:\Python38\python.exe" set "PYTHON_EXE=C:\Python38\python.exe"
        ) else (
            if exist "C:\Python38-32\python.exe" set "PYTHON_EXE=C:\Python38-32\python.exe"
        )
    ) else (
        echo   [错误] Python 安装失败 (错误码 %errorlevel%)
        pause
        exit /b 1
    )
)

rem 验证 Python 可用
"%PYTHON_EXE%" --version 2>&1
if %errorlevel% neq 0 (
    echo   [错误] Python 无法运行
    pause
    exit /b 1
)
echo.

rem ============================================================
rem 第3步: 枚举磁盘 (可移动 + 本地磁盘, 排除系统盘)
rem ============================================================
echo [3/4] 检测可移动磁盘...
set "SYS_DRIVE=%SystemDrive%"
set "SYS_DRIVE=%SYS_DRIVE:~0,1%"
echo   系统盘: %SYS_DRIVE%:

set "DRV_COUNT=0"
set "DRV_LIST="

rem 用 wmic 枚举逻辑盘: 2=可移动, 3=本地磁盘
for /f "skip=1 tokens=1-4 delims=," %%a in ('wmic logicaldisk get deviceid^,drivetype^,volumename /format:csv 2^>nul') do (
    set "DISK_ID=%%b"
    set "DISK_TYPE=%%c"
    set "DISK_VOL=%%d"
    rem 跳过表头和空行
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

if %DRV_COUNT%==0 (
    echo   [警告] 未检测到可移动磁盘或本地磁盘 (已排除系统盘 %SYS_DRIVE%)
    echo   请确认移动硬盘/U盘已连接并识别。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   请选择要测试的磁盘:
echo   (提示: USB移动硬盘通常显示为"本地磁盘", U盘显示为"可移动磁盘")
echo ============================================================
set "CHOICE="
set /p "CHOICE=请输入磁盘编号 (1-%DRV_COUNT%): "

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

rem 确认目标盘可写
if not exist "%TARGET_DRIVE%:\" (
    echo   [错误] 磁盘 %TARGET_DRIVE%: 不可访问
    pause
    exit /b 1
)
echo.

rem ============================================================
rem 第4步: 设定循环次数, 拷贝脚本, 运行
rem ============================================================
echo [4/4] 准备测试...
set "CYCLES="
set /p "CYCLES=请输入测试循环次数 (1-9999, 建议1-10): "
if not defined CYCLES set "CYCLES=1"
set /a CYCLES_NUM=0
for /f "delims=0123456789" %%i in ("%CYCLES%") do set "CYCLES_NUM=0"
set /a CYCLES_NUM=%CYCLES% 2>nul
if %CYCLES_NUM% lss 1 set "CYCLES_NUM=1"
if %CYCLES_NUM% gtr 9999 set "CYCLES_NUM=9999"

rem 拷贝测试脚本到目标盘根目录
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

rem 运行测试脚本 (自动确认, 无需再输入)
"%PYTHON_EXE%" "%TARGET_DRIVE%:\%PY_SCRIPT%" --cycles %CYCLES_NUM% --yes

echo.
echo ============================================================
echo   测试流程结束
echo ============================================================
pause
exit /b 0
