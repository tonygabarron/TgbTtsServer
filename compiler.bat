@echo off
cls
title TgbTtsServer Compiler

:: Navega para o diretório do script
cd /d "%~dp0"

echo =======================================================
echo  TgbTtsServer - Compilador para Executavel (.exe)
echo =======================================================
echo.
echo Este script ira transformar o programa Python em um
echo unico arquivo executavel (.exe) usando o PyInstaller.
echo.
echo O processo pode levar alguns minutos.
echo.
pause
cls

:: --- PASSO 1: CONFIGURAR AMBIENTE DE COMPILACAO ---
set VENV_DIR=compiler_venv
echo.
echo --- PASSO 1: Configurando o ambiente de compilacao (%VENV_DIR%)...
if not exist "%VENV_DIR%\Scripts\activate" (
    echo Criando novo ambiente virtual...
    py -m venv %VENV_DIR%
    if %ERRORLEVEL% NEQ 0 (
        echo X Falha ao criar o ambiente virtual. Abortando. X
        pause
        exit /b
    )
) else (
    echo Ambiente virtual ja existe.
)

:: Ativa o ambiente virtual
call "%VENV_DIR%\Scripts\activate"

:: --- PASSO 2: INSTALAR FERRAMENTAS E DEPENDENCIAS ---
echo.
echo --- PASSO 2: Instalando PyInstaller e dependencias do aplicativo...
echo.

:: Instala o PyInstaller E TODAS as bibliotecas que seu aplicativo precisa.
pip install pyinstaller customtkinter requests Flask Flask-Cors gtts

if %ERRORLEVEL% NEQ 0 (
    echo X Falha ao instalar as dependencias. Verifique sua conexao com a internet. X
    pause
    exit /b
)
echo Ferramentas e dependencias estao prontas.

:: --- PASSO 3: PREPARAR E COMPILAR O SCRIPT ---
set APP_NAME=TgbTtsServer
:: ATENÇÃO: Verifique se o nome do arquivo .pyw está correto aqui.
set SCRIPT_NAME=tgb_tts_server.pyw
set ICON_FILE=app_icon.ico
set ICON_ARG=

echo.
echo --- PASSO 3: Compilando o aplicativo...
echo Nome do App: %APP_NAME%
echo Script: %SCRIPT_NAME%

:: Verifica se o arquivo de ícone existe para adicioná-lo ao comando
if exist "%ICON_FILE%" (
    echo Icone '%ICON_FILE%' encontrado e sera usado.
    set ICON_ARG=--icon="%ICON_FILE%"
) else (
    echo AVISO: Icone '%ICON_FILE%' nao encontrado. Um icone padrao sera usado.
)
echo.
echo O PyInstaller esta trabalhando. Isto pode demorar...
echo.

:: MODIFICADO: Executa o PyInstaller SEM o --add-data para o config.ini
pyinstaller --noconsole --onefile --name "%APP_NAME%" %ICON_ARG% "%SCRIPT_NAME%"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo X ERRO DURANTE A COMPILACAO! X
    echo Verifique as mensagens de erro acima.
    pause
    exit /b
)

:: Desativa o ambiente virtual
deactivate

:: --- PASSO 4: LIMPEZA E FINALIZAÇÃO ---
cls
echo.
echo --- PASSO 4: Limpando arquivos temporarios...
echo.

rmdir /s /q build
del /q "%APP_NAME%.spec"
if exist "dist\%APP_NAME%.exe" (
    move "dist\%APP_NAME%.exe" .
)
rmdir /q dist 2>nul

echo Limpeza concluida.

echo.
echo =======================================================
echo  COMPILACAO CONCLUIDA COM SUCESSO!
echo =======================================================
echo.
echo O seu aplicativo esta pronto: %APP_NAME%.exe
echo.
pause