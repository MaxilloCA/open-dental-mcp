$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$taskEnv = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Install the local virtual environment before starting the server.'
}
& $taskPython -c 'from open_dental_mcp.server import main; main()' --env-file $taskEnv
exit $LASTEXITCODE
