param(
  [string]$WindowQuery = "",
  [string]$Label = "",
  [string]$ControlType = "",
  [ValidateSet("invoke","toggle","set_text","select","expand","collapse","focus","read")]
  [string]$Action = "invoke",
  [string]$DesiredState = "",
  [string]$Value = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName System.Windows.Forms

$code=@'
using System;
using System.Runtime.InteropServices;
public class SHALUIHost {
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
 [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd,int nCmdShow);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int X,int Y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint dx,uint dy,uint data,UIntPtr extra);
}
'@
Add-Type $code -ErrorAction SilentlyContinue

function Get-TargetProcess([string]$query) {
  if(-not $query) {
    $h=[SHALUIHost]::GetForegroundWindow()
    if($h -eq [IntPtr]::Zero){ throw "No active window." }
    return Get-Process | Where-Object { $_.MainWindowHandle -eq $h } | Select-Object -First 1
  }
  $q=$query.Trim()
  $rx=[regex]::Escape($q)
  $matches=Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle } | ForEach-Object {
    $score=99
    if($_.MainWindowTitle -ieq $q){$score=0}
    elseif($_.ProcessName -ieq $q){$score=1}
    elseif($_.MainWindowTitle.StartsWith($q,[StringComparison]::OrdinalIgnoreCase)){$score=2}
    elseif($_.ProcessName.StartsWith($q,[StringComparison]::OrdinalIgnoreCase)){$score=3}
    elseif($_.MainWindowTitle.IndexOf($q,[StringComparison]::OrdinalIgnoreCase) -ge 0){$score=4}
    elseif($_.ProcessName.IndexOf($q,[StringComparison]::OrdinalIgnoreCase) -ge 0){$score=5}
    elseif($_.MainWindowTitle -match "(?i)(^|[^A-Za-z0-9])$rx([^A-Za-z0-9]|$)"){$score=6}
    if($score -lt 99){[pscustomobject]@{Score=$score;P=$_}}
  }
  $p=($matches | Sort-Object Score | Select-Object -First 1).P
  if(-not $p){ throw "No open window matched '$q'." }
  return $p
}

function Get-MatchScore($e,[string]$needle,[string]$role,[string]$action) {
  try {
    $name=$e.Current.Name
    $aid=$e.Current.AutomationId
    $type=$e.Current.ControlType.ProgrammaticName -replace '^ControlType\.',''
    $enabled=$e.Current.IsEnabled
    $off=$e.Current.IsOffscreen
  } catch { return $null }
  if(-not $enabled){ return $null }
  if($role -and $type -notlike "*$role*"){ return $null }
  if($action -eq "set_text" -and $type -notin @("Edit","ComboBox","Document")){ return $null }
  if($action -eq "toggle" -and $type -notin @("CheckBox","Button")){ return $null }
  if($action -eq "select" -and $type -notin @("ComboBox","List","ListItem")){ return $null }

  $score=99
  if(-not $needle){
    if($action -eq "set_text" -and $type -in @("Edit","ComboBox","Document")){$score=8}
    elseif($action -eq "select" -and $type -in @("ComboBox","List","ListItem")){$score=8}
    else{return $null}
  } elseif($name -ieq $needle -or $aid -ieq $needle){$score=0}
  elseif($name -and $name.StartsWith($needle,[StringComparison]::OrdinalIgnoreCase)){$score=1}
  elseif($name -and $name.IndexOf($needle,[StringComparison]::OrdinalIgnoreCase) -ge 0){$score=2}
  elseif($aid -and $aid.IndexOf($needle,[StringComparison]::OrdinalIgnoreCase) -ge 0){$score=3}
  if($score -ge 99){return $null}
  if($off){$score+=20}
  if($action -eq "invoke" -and $type -in @("Button","Hyperlink","MenuItem","TabItem","TreeItem","ListItem","RadioButton")){$score-=2}
  if($action -eq "toggle" -and $type -in @("CheckBox","Button")){$score-=2}
  if($action -eq "set_text" -and $type -in @("Edit","ComboBox","Document")){$score-=2}
  return [pscustomobject]@{Score=$score;E=$e;Name=$name;AutomationId=$aid;Type=$type;Offscreen=$off}
}

$p=Get-TargetProcess $WindowQuery
if(-not $p){throw "Target window process was not found."}
[SHALUIHost]::ShowWindowAsync($p.MainWindowHandle,9)|Out-Null
[SHALUIHost]::SetForegroundWindow($p.MainWindowHandle)|Out-Null
Start-Sleep -Milliseconds 120
$root=[System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
if(-not $root){throw "Could not create UI Automation root."}
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
$candidates=@()
foreach($e in $all){
  $m=Get-MatchScore $e $Label $ControlType $Action
  if($m){$candidates+=$m}
}
if(-not $candidates){throw "Control '$Label' was not found in '$($p.MainWindowTitle)'."}

$done=$false
$pick=$null
$state=""
$outValue=""

foreach($m in ($candidates | Sort-Object Score)){
  $e=$m.E
  try {
    switch($Action){
      "read" {
        $pick=$m;$done=$true
      }
      "focus" {
        $e.SetFocus();$pick=$m;$done=$true
      }
      "invoke" {
        try{$pat=$e.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern);if($pat){$pat.Invoke();$done=$true}}catch{}
        if(-not $done){try{$pat=$e.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern);if($pat){$pat.Select();$done=$true}}catch{}}
        if(-not $done){try{$pat=$e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern);if($pat){$pat.Toggle();$done=$true}}catch{}}
        if(-not $done){try{$pat=$e.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern);if($pat){$pat.Expand();$done=$true}}catch{}}
        if(-not $done){
          try{
            $r=$e.Current.BoundingRectangle
            if($r.Width -gt 2 -and $r.Height -gt 2 -and -not $e.Current.IsOffscreen){
              [SHALUIHost]::SetCursorPos([int]($r.X+$r.Width/2),[int]($r.Y+$r.Height/2))|Out-Null
              Start-Sleep -Milliseconds 70
              [SHALUIHost]::mouse_event(0x0002,0,0,0,[UIntPtr]::Zero)
              [SHALUIHost]::mouse_event(0x0004,0,0,0,[UIntPtr]::Zero)
              $done=$true
            }
          }catch{}
        }
        if($done){$pick=$m}
      }
      "toggle" {
        $pat=$e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
        if(-not $pat){continue}
        $before=$pat.Current.ToggleState.ToString()
        if($DesiredState){
          $wantOn=$DesiredState -ieq "on"
          $isOn=$before -eq "On"
          if($wantOn -ne $isOn){$pat.Toggle();Start-Sleep -Milliseconds 180}
        } else {$pat.Toggle();Start-Sleep -Milliseconds 180}
        $after=$pat.Current.ToggleState.ToString()
        if($DesiredState){
          $want=if($DesiredState -ieq "on"){"On"}else{"Off"}
          if($after -ne $want){throw "Toggle verification failed. Expected $want but found $after."}
        }
        $state=$after;$pick=$m;$done=$true
      }
      "set_text" {
        $e.SetFocus()
        $set=$false
        try{
          $vp=$e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
          if($vp -and -not $vp.Current.IsReadOnly){$vp.SetValue($Value);$set=$true;Start-Sleep -Milliseconds 100;$outValue=$vp.Current.Value}
        }catch{}
        if(-not $set){
          Set-Clipboard -Value $Value
          [System.Windows.Forms.SendKeys]::SendWait('^a')
          Start-Sleep -Milliseconds 60
          [System.Windows.Forms.SendKeys]::SendWait('^v')
          Start-Sleep -Milliseconds 100
          try{$vp=$e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern);if($vp){$outValue=$vp.Current.Value}}catch{}
          $set=$true
        }
        if($set){$pick=$m;$done=$true}
      }
      "select" {
        $selected=$false
        try{
          $ep=$e.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
          if($ep){$ep.Expand();Start-Sleep -Milliseconds 150}
        }catch{}
        $scope=$e.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
        foreach($child in $scope){
          try{$cn=$child.Current.Name;$ct=$child.Current.ControlType.ProgrammaticName}catch{continue}
          if($cn -ieq $Value -or ($cn -and $cn.IndexOf($Value,[StringComparison]::OrdinalIgnoreCase) -ge 0)){
            try{$sp=$child.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern);if($sp){$sp.Select();$selected=$true;$outValue=$cn;break}}catch{}
            try{$ip=$child.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern);if($ip){$ip.Invoke();$selected=$true;$outValue=$cn;break}}catch{}
          }
        }
        if(-not $selected){continue}
        $pick=$m;$done=$true
      }
      "expand" {
        $ep=$e.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
        if(-not $ep){continue};$ep.Expand();$pick=$m;$done=$true
      }
      "collapse" {
        $ep=$e.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
        if(-not $ep){continue};$ep.Collapse();$pick=$m;$done=$true
      }
    }
  } catch {
    if($Action -in @("toggle","set_text","select")){throw}
  }
  if($done){break}
}

if(-not $done){throw "Control '$Label' cannot perform action '$Action'."}

try{
  if(-not $state){
    $tp=$pick.E.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
    if($tp){$state=$tp.Current.ToggleState.ToString()}
  }
}catch{}
try{
  if(-not $outValue){
    $vp=$pick.E.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if($vp){$outValue=$vp.Current.Value}
  }
}catch{}

[pscustomobject]@{
  Ok=$true
  Window=$p.MainWindowTitle
  Process=$p.ProcessName
  Control=$pick.Name
  AutomationId=$pick.AutomationId
  Type=$pick.Type
  Action=$Action
  State=$state
  Value=$outValue
} | ConvertTo-Json -Compress
