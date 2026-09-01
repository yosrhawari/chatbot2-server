# HAYETT Admin Tools — Build 4 exe avec PyInstaller
# Usage: .\build_exes.ps1  (depuis la racine du projet)
# Prérequis: backend\venv\Scripts\python.exe avec pyinstaller, bcrypt, oracledb

$ErrorActionPreference = "Stop"
$PY = "backend\venv\Scripts\python.exe"
$DIST = "HAYETT_Admin_Tools"
$BUILD = "build"

# Nettoyage
if (Test-Path $DIST) { Remove-Item $DIST -Recurse -Force }
if (Test-Path $BUILD) { Remove-Item $BUILD -Recurse -Force }
if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }

$common = @("--onefile", "--windowed", "--clean", "--paths=backend", "--hidden-import=config", "--hidden-import=security", "--hidden-import=database", "--hidden-import=email_service", "--hidden-import=gui_common", "--hidden-import=tools.gui_common", "--hidden-import=bcrypt", "--hidden-import=oracledb", "--hidden-import=cryptography", "--hidden-import=cryptography.hazmat.primitives.kdf.pbkdf2", "--hidden-import=cffi", "--hidden-import=secrets", "--hidden-import=asyncio", "--hidden-import=uuid", "--hidden-import=concurrent.futures", "--hidden-import=hmac", "--hidden-import=hashlib", "--hidden-import=ssl", "--hidden-import=email", "--hidden-import=email.message", "--hidden-import=smtplib", "--hidden-import=socket", "--collect-submodules=oracledb", "--collect-submodules=bcrypt", "--collect-submodules=cryptography", "--collect-submodules=cffi")

Write-Host "=== Build HAYETT_Create_Client ==="
& $PY -m PyInstaller @common --name "HAYETT_Create_Client" --distpath $DIST --workpath $BUILD --specpath $BUILD "backend/tools/create_client_account.py"

Write-Host "=== Build HAYETT_Create_Contrat ==="
& $PY -m PyInstaller @common --name "HAYETT_Create_Contrat" --distpath $DIST --workpath $BUILD --specpath $BUILD "backend/tools/create_contrat.py"

Write-Host "=== Build HAYETT_Create_Versement ==="
& $PY -m PyInstaller @common --name "HAYETT_Create_Versement" --distpath $DIST --workpath $BUILD --specpath $BUILD "backend/tools/create_versement.py"

Write-Host "=== Build HAYETT_Create_Beneficiaire ==="
& $PY -m PyInstaller @common --name "HAYETT_Create_Beneficiaire" --distpath $DIST --workpath $BUILD --specpath $BUILD "backend/tools/create_beneficiaire.py"

# Copier .env à côté des exe (jamais embarqué)
Copy-Item "backend\.env" "$DIST\.env" -Force
# README
@"
HAYETT Admin Tools — Utilisation
================================
1. Placez le dossier HAYETT_Admin_Tools où vous voulez (ex: Bureau).
2. Vérifiez que .env est à côté des 4 .exe (même dossier).
3. Configurez .env : ORACLE_* et SMTP_* (jamais en dur dans l'exe).
4. Double-cliquez :
   - HAYETT_Create_Client.exe      -> création client+compte (CIN, email, MdP masqué)
   - HAYETT_Create_Contrat.exe     -> CIN -> contrat C001...
   - HAYETT_Create_Versement.exe   -> CIN -> choix contrat filtré -> versement
   - HAYETT_Create_Beneficiaire.exe-> CIN -> choix contrat filtré -> bénéficiaire
5. En cas d'erreur, une fenêtre s'affiche (jamais de MdP dans le message).
6. Rebuild : .\build_exes.ps1  (ou: backend\venv\Scripts\python.exe -m PyInstaller ...)
"@ | Set-Content "$DIST\README.txt" -Encoding UTF8

Write-Host "`nBuild terminé. Contenu de $DIST :"
Get-ChildItem $DIST | Format-Table Name, Length
