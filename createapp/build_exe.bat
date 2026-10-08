@echo off
echo ===================================================
echo   Creation de l'application Executable (.exe)
echo ===================================================
echo.

:: Verifier si PyInstaller est installe
python -m pip install pyinstaller oracledb bcrypt cryptography --quiet

echo Compilation de Client Manager...
pyinstaller --noconsole --onefile --clean --collect-all cryptography --name "HAYETT_Client_Manager_v2" create_client_account.py

echo Compilation de Create Contrat...
pyinstaller --noconsole --onefile --clean --collect-all cryptography --name "HAYETT_Create_Contrat" create_contrat_app.py

echo Compilation de Create Versement...
pyinstaller --noconsole --onefile --clean --collect-all cryptography --name "HAYETT_Create_Versement" create_versement_app.py

echo Compilation de Create Epargne...
pyinstaller --noconsole --onefile --clean --collect-all cryptography --name "HAYETT_Create_Epargne" create_epargne_app.py

echo Compilation de Create Beneficiaire...
pyinstaller --noconsole --onefile --clean --collect-all cryptography --name "HAYETT_Create_Beneficiaire" create_beneficiaire_app.py

echo.
echo ===================================================
echo   TERMINE !
echo   Votre application se trouve dans le dossier 'dist' :
echo   dist\HAYETT_Client_Manager.exe
echo ===================================================
