@echo off
rem Installe Farever+ pour l'utilisateur courant, en un double-clic :
rem   1. verifie que Python est la ;
rem   2. installe ses modules (Frida en 17.18.0 : la 17.19.0 fait planter le jeu
rem      a la fermeture de Farever+) ;
rem   3. cree le raccourci " Farever+ " sur le Bureau et dans le menu Demarrer,
rem      qui lance l'application sans fenetre de console.
rem Relancable sans risque : il ne fait que remettre les choses en place.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Installation de Farever+

where py >nul 2>nul
if errorlevel 1 (
    echo.
    echo  Python n'est pas installe.
    echo  Installe-le depuis https://www.python.org/downloads/ puis relance ce fichier.
    echo.
    pause
    exit /b 1
)

echo.
echo  == Modules Python (frida 17.18.0, pillow, pywebview)
py -m pip install --disable-pip-version-check -q frida==17.18.0 pillow pywebview
if errorlevel 1 (
    echo.
    echo  L'installation des modules a echoue : le detail est ci-dessus.
    pause
    exit /b 1
)

echo  == Raccourcis (Bureau et menu Demarrer)
py packaging\raccourci.py
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)

echo.
echo  Farever+ est installe. Lance-le avec le raccourci « Farever+ » du Bureau.
echo  Au premier lancement avec le jeu, il prepare ses donnees (1 a 2 minutes).
echo.
pause
