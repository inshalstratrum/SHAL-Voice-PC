$ErrorActionPreference = 'Stop'

if (Get-Command 'adb' -ErrorAction SilentlyContinue) {
    $adb = 'adb'
} elseif (Test-Path 'E:\SDK\android-sdk\platform-tools\adb.exe') {
    $adb = 'E:\SDK\android-sdk\platform-tools\adb.exe'
} elseif (Test-Path "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe") {
    $adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
} else {
    throw "ADB not found. Please install Android Platform Tools and ensure adb is on your PATH."
}
$pkg = 'com.shal.voice.shal_voice_pc'
$helperPkgs = @($pkg,'com.tailscale.ipn','com.carriez.flutter_hbb')

$devices = & $adb devices
if(-not ($devices -match '\tdevice$')){
    throw 'No authorized Android device is connected by ADB.'
}

$installed = & $adb shell pm list packages $pkg
if(-not ($installed -match [regex]::Escape($pkg))){
    Write-Output 'SHAL Voice PC is not installed on the connected phone.'
    Write-Output 'Install SHAL-Voice-PC-v0.2.0.apk, then run Phone Post-Setup again.'
    exit 2
}

$version = (& $adb shell dumpsys package $pkg | Select-String 'versionName=' | Select-Object -First 1).Line.Trim()
Write-Output "Detected $pkg  $version"

# Check the microphone permission first. POCO/MIUI may deliberately block shell grants
# unless "USB debugging (Security settings)" is enabled. That is not fatal: the app can
# request the permission normally from Android.
$permLine = (& $adb shell dumpsys package $pkg | Select-String 'android.permission.RECORD_AUDIO: granted=' | Select-Object -First 1).Line
$micGranted = [bool]($permLine -match 'granted=true')

if(-not $micGranted){
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $grantText = (& $adb shell pm grant $pkg android.permission.RECORD_AUDIO 2>&1 | Out-String).Trim()
    $grantCode = $LASTEXITCODE
    $ErrorActionPreference = $oldEap

    $permLine = (& $adb shell dumpsys package $pkg | Select-String 'android.permission.RECORD_AUDIO: granted=' | Select-Object -First 1).Line
    $micGranted = [bool]($permLine -match 'granted=true')

    if(-not $micGranted){
        Write-Output 'Microphone permission was NOT granted by ADB.'
        if($grantText -match 'GRANT_RUNTIME_PERMISSIONS'){
            Write-Output 'POCO/MIUI blocked shell runtime-permission grants. This is expected when USB debugging (Security settings) is unavailable/disabled.'
        } elseif($grantText) {
            Write-Output ("ADB grant response: " + (($grantText -split "\r?\n")[0]))
        }

        # Open SHAL Voice PC's Android app settings so the required permission change is visible.
        & $adb shell am start -a android.settings.APPLICATION_DETAILS_SETTINGS -d "package:$pkg" | Out-Null
        Write-Output 'Opened SHAL Voice PC App Info on the phone.'
        Write-Output 'On the phone: Permissions -> Microphone -> Allow only while using the app.'
    }
}

# Best-effort background allowances. These are safe to repeat.
foreach($p in $helperPkgs){
    $exists = & $adb shell pm list packages $p
    if($exists -match [regex]::Escape($p)){
        $oldEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $adb shell cmd appops set $p RUN_ANY_IN_BACKGROUND allow 2>$null | Out-Null
        & $adb shell dumpsys deviceidle whitelist +$p 2>$null | Out-Null
        $ErrorActionPreference = $oldEap
    }
}

# Bring Tailscale to the foreground once. On this POCO, launching the app has been enough
# to wake a previously-idle Tailscale VPN service without changing account or routing settings.
$oldEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $adb shell monkey -p com.tailscale.ipn -c android.intent.category.LAUNCHER 1 2>$null | Out-Null
$ErrorActionPreference = $oldEap
Start-Sleep -Seconds 4

# Re-check and report the actual resulting state.
$permLine = (& $adb shell dumpsys package $pkg | Select-String 'android.permission.RECORD_AUDIO: granted=' | Select-Object -First 1).Line
$micGranted = [bool]($permLine -match 'granted=true')
$bgOp = (& $adb shell cmd appops get $pkg RUN_ANY_IN_BACKGROUND 2>$null | Out-String).Trim()
$whitelist = (& $adb shell dumpsys deviceidle whitelist | Select-String $pkg | Out-String).Trim()

$ip = (& tailscale ip -4 | Select-Object -First 1).Trim()

Write-Output ''
Write-Output 'Phone post-install setup completed.'
Write-Output ("Microphone permission: " + ($(if($micGranted){'GRANTED'}else{'NEEDS PHONE APPROVAL'})))
Write-Output ("Background app-op: " + ($(if($bgOp){$bgOp}else{'not reported'})))
Write-Output ("Doze whitelist: " + ($(if($whitelist){'YES'}else{'NO'})))
$phoneTail = (& tailscale status | Select-String 'poco-c65' | Select-Object -First 1).Line
$phoneOnline = [bool]($phoneTail -and $phoneTail -notmatch 'offline')

Write-Output ("Voice PC server: http://{0}:8765" -f $ip)
Write-Output ("Phone Tailscale: " + ($(if($phoneOnline){'ONLINE'}else{'OFFLINE - open Tailscale on the phone and switch it on'})))

if($micGranted){
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $adb shell monkey -p $pkg -c android.intent.category.LAUNCHER 1 2>$null | Out-Null
    $ErrorActionPreference = $oldEap
    Write-Output 'Opened SHAL Voice PC on the phone.'
}else{
    Write-Output 'After allowing Microphone on the phone, return to SHAL Voice PC and tap the microphone once to verify.'
}
