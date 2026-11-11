param([switch]$OpenReview)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$root = [Windows.Automation.AutomationElement]::RootElement
$windows = $root.FindAll([Windows.Automation.TreeScope]::Children,[Windows.Automation.Condition]::TrueCondition)
$window = $null
foreach ($candidate in $windows) { if ($candidate.Current.Name -match 'DeepSeek Harness$') { $window = $candidate; break } }
if (-not $window) { throw 'DeepSeek Harness window not found' }
Write-Output ('Window: ' + $window.Current.Name)
$controls = $window.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition)
if ($OpenReview) {
  $label = [string][char]0x56fe + [char]0x7247 + [char]0x5ba1 + [char]0x9605
  $found = $false
  foreach ($control in $controls) {
    if ($control.Current.Name -eq $label -and $control.Current.ControlType.ProgrammaticName -eq 'ControlType.Button') {
      $pattern = $null
      if ($control.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) { $pattern.Invoke(); $found = $true; break }
    }
  }
  if (-not $found) { throw 'Image Review sidebar button not found' }
  Write-Output 'Invoked Image Review sidebar'
}
$count = 0
foreach ($control in $controls) {
  $name = $control.Current.Name
  $kind = $control.Current.ControlType.ProgrammaticName
  if ($name -and $name.Length -lt 240 -and $kind -notmatch 'ListItem') {
    if ($kind -match 'Button|ComboBox|CheckBox|Document|Window' -or $name -match '\u5ba1\u9605|\u5173\u8054\u4f1a\u8bdd|\u670d\u52a1|\u5de5\u4f5c\u53f0') {
      Write-Output ($kind + ' | ' + $name)
      $count++
      if ($count -ge 90) { break }
    }
  }
}
