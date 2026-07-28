$JAVA_HOME = "C:\Program Files\BellSoft\LibericaJDK-11-Full"
$AUTOMAKER_DIR = "$env:USERPROFILE\Documents\CEL Robox\AutoMaker"
$CONFIG_FILE = "$AUTOMAKER_DIR\AutoMaker.configFile.xml"

& "$JAVA_HOME\bin\java.exe" "-DlibertySystems.configFile=$CONFIG_FILE" -jar "$AUTOMAKER_DIR\AutoMaker.jar"
