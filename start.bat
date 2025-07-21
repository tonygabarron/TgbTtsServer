@echo off
cls
title TgbTtsServer Installer & Launcher

:: Navega para o diretório onde o script está localizado
cd /d "%~dp0"

echo =======================================================
echo  TgbTtsServer - Verificador de Ambiente e Inicializador
echo =======================================================
echo.
cls

:: --- PASSO 1: VERIFICAR SE O PYTHON ESTÁ INSTALADO ---
echo.
echo --- PASSO 1: Verificando a instalacao do Python...
echo.

:: O comando 'where' procura pelo executável no PATH do sistema.
:: Redirecionamos a saída e o erro para NUL para não poluir a tela.
where python >nul 2>nul

:: A variável %ERRORLEVEL% é 0 se o comando anterior foi bem-sucedido.
:: Se for diferente de 0, o Python não foi encontrado.
if %ERRORLEVEL% NEQ 0 (
    goto :INSTALL_PYTHON
) else (
    echo Python encontrado! Prosseguindo...
    goto :CHECK_VENV
)

:INSTALL_PYTHON
cls
echo ========================================================
echo  ERRO: Python nao encontrado!
echo ========================================================
echo.
echo O Python nao parece estar instalado ou adicionado ao PATH do sistema.
echo.
echo --- OPCAO RECOMENDADA ---
echo 1. Abra a Microsoft Store e instale a versao mais recente do Python.
echo    Voce pode abrir a loja diretamente com este comando:
echo.
start ms-windows-store://pdp/?productid=9P7QFQCS8T5K
echo.
echo 2. IMPORTANTE: Durante a instalacao pelo site python.org, certifique-se
echo    de marcar a caixa "Add Python to PATH".
echo.
echo --------------------------------------------------------
echo.
echo >> Depois de instalar o Python, FECHE esta janela e execute o start.bat novamente. <<
echo.
pause
exit /b


:: --- PASSO 2: VERIFICAR SE O AMBIENTE VIRTUAL (VENV) EXISTE ---
:CHECK_VENV
cls
echo.
echo --- PASSO 2: Verificando o ambiente virtual (venv)...
echo.

:: Verifica a existência do executável Python dentro da pasta venv.
if not exist "venv\Scripts\python.exe" (
    echo Ambiente virtual nao encontrado. Criando agora...
    goto :CREATE_VENV
) else (
    echo Ambiente virtual encontrado. Prosseguindo...
    goto :INSTALL_DEPS
)

:CREATE_VENV
:: Cria o ambiente virtual usando o Python principal do sistema.
py -m venv venv

:: Verifica se a criação foi bem-sucedida.
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo X FALHA AO CRIAR O AMBIENTE VIRTUAL. X
    echo Verifique sua instalacao do Python e tente novamente.
    echo.
    pause
    exit /b
)
echo Ambiente virtual criado com sucesso!
goto :INSTALL_DEPS


:: --- PASSO 3: INSTALAR DEPENDÊNCIAS DENTRO DO VENV ---
:INSTALL_DEPS
cls
echo.
echo --- PASSO 3: Instalando/Verificando as dependencias...
echo.

:: Usa o pip de dentro do venv para instalar os pacotes.
venv\Scripts\pip.exe install customtkinter requests Flask Flask-Cors gtts

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo X FALHA AO INSTALAR AS DEPENDENCIAS. X
    echo Verifique sua conexao com a internet ou se o pip esta bloqueado por um firewall.
    echo.
    pause
    exit /b
)
echo Dependencias instaladas com sucesso!
echo.


:: --- PASSO 4: EXECUTAR O APLICATIVO ---
:RUN_APP
echo.
echo =======================================================
echo  Tudo pronto! Iniciando o aplicativo...
echo =======================================================
echo.
echo Executando o script com o 'python.exe' para exibir o console
echo e possiveis mensagens de erro.
echo.

:: Usa o python.exe de dentro do venv para executar o script da GUI.
venv\Scripts\python.exe tgb_tts_server.pyw

echo.
echo --- O script terminou. Pressione qualquer tecla para fechar esta janela. ---
pause