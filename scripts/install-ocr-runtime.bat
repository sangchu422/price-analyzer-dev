@echo off
chcp 65001 >nul
setlocal EnableExtensions

for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
set "TESSERACT_EXE=%ProgramFiles%\Tesseract-OCR\tesseract.exe"
set "SYSTEM_TESSDATA=%ProgramFiles%\Tesseract-OCR\tessdata"
set "LOCAL_TESSDATA=%REPO_ROOT%\backend\.local\ocr\tessdata"
set "KOR_MODEL_URL=https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/kor.traineddata"

if not exist "%TESSERACT_EXE%" (
  where winget.exe >nul 2>&1
  if errorlevel 1 (
    echo winget.exe was not found. Install Tesseract 5 and run this script again.
    exit /b 1
  )
  echo Installing Tesseract OCR...
  winget install --id UB-Mannheim.TesseractOCR --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
  if errorlevel 1 (
    echo Tesseract OCR installation failed.
    exit /b 1
  )
)

if not exist "%LOCAL_TESSDATA%" mkdir "%LOCAL_TESSDATA%"
if not exist "%LOCAL_TESSDATA%\eng.traineddata" copy /Y "%SYSTEM_TESSDATA%\eng.traineddata" "%LOCAL_TESSDATA%\eng.traineddata" >nul
if not exist "%LOCAL_TESSDATA%\osd.traineddata" copy /Y "%SYSTEM_TESSDATA%\osd.traineddata" "%LOCAL_TESSDATA%\osd.traineddata" >nul

if not exist "%LOCAL_TESSDATA%\kor.traineddata" (
  where curl.exe >nul 2>&1
  if errorlevel 1 (
    echo curl.exe was not found. The Korean OCR model cannot be downloaded.
    exit /b 1
  )
  echo Installing the Korean OCR model...
  curl.exe -fL --retry 2 --output "%LOCAL_TESSDATA%\kor.traineddata" "%KOR_MODEL_URL%"
  if errorlevel 1 (
    echo Korean OCR model download failed.
    exit /b 1
  )
)

"%TESSERACT_EXE%" --tessdata-dir "%LOCAL_TESSDATA%" --list-langs
if errorlevel 1 exit /b 1

echo OCR runtime is ready.
echo Tesseract: %TESSERACT_EXE%
echo Language models: %LOCAL_TESSDATA%
exit /b 0
