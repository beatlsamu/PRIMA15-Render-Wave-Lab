@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Se creo .env desde .env.example.
)
findstr /C:"PEGA_TU_CLAVE_AQUI" ".env" >nul 2>nul
if not errorlevel 1 (
  echo Pega tu clave de OpenRouter en OPENROUTER_API_KEY. Guarda y cierra el Bloc de notas para continuar.
  notepad ".env"
)
if not exist ".venv\Scripts\python.exe" (
  echo Creando entorno virtual con Python 3.11...
  py -3.11 -m venv .venv
  if errorlevel 1 goto error
)
echo Instalando o verificando dependencias...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error
echo Iniciando PRIMA-15 en http://127.0.0.1:5000
.venv\Scripts\python.exe app.py
goto end
:error
echo.
echo No se pudo iniciar. Verifica que Python 3.11 este instalado y disponible como py -3.11.
pause
:end
endlocal
