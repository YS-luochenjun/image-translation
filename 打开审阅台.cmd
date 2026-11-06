@echo off
setlocal
cd /d "%~dp0"
if defined IMAGE_TRANSLATOR_PYTHON goto custom_python
if exist "%~dp0.venv\Scripts\python.exe" goto project_python
if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" goto harness_python
python "%~dp0bin\it.py" review-desk --open %*
goto finish
:custom_python
"%IMAGE_TRANSLATOR_PYTHON%" "%~dp0bin\it.py" review-desk --open %*
goto finish
:project_python
"%~dp0.venv\Scripts\python.exe" "%~dp0bin\it.py" review-desk --open %*
goto finish
:harness_python
"%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" "%~dp0bin\it.py" review-desk --open %*
:finish
if errorlevel 1 pause
