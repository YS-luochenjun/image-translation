$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$localPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$harnessPython = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
if ($env:IMAGE_TRANSLATOR_PYTHON) {
    $pythonExecutable = $env:IMAGE_TRANSLATOR_PYTHON
} elseif (Test-Path -LiteralPath $localPython) {
    $pythonExecutable = $localPython
} elseif (Test-Path -LiteralPath $harnessPython) {
    $pythonExecutable = $harnessPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw 'Python not found. Install Python 3.11+ or set IMAGE_TRANSLATOR_PYTHON to its full path.'
    }
    $pythonExecutable = $pythonCommand.Source
}
& $pythonExecutable (Join-Path $PSScriptRoot 'it.py') @args
exit $LASTEXITCODE
