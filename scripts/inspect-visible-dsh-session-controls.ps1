Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$taskRoot = [System.Windows.Automation.AutomationElement]::RootElement
$taskWindows = $taskRoot.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
$taskResult = @()
foreach ($taskWindow in $taskWindows) {
  $taskName = $taskWindow.Current.Name
  if ($taskName -notmatch 'DeepSeek Harness|单图片翻译|DSH') { continue }
  $taskItems = @()
  $taskElements = $taskWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  for ($taskIndex=0; $taskIndex -lt [Math]::Min($taskElements.Count, 1600); $taskIndex++) {
    $taskElement = $taskElements.Item($taskIndex)
    $taskCurrent = $taskElement.Current
    if ($taskCurrent.Name -match '043|会话|新建|Session|session|聊天|New chat|插件|Plugin' -or $taskCurrent.ControlType.ProgrammaticName -eq 'ControlType.Edit') {
      $taskItems += @{ name=$taskCurrent.Name; automationId=$taskCurrent.AutomationId; type=$taskCurrent.ControlType.ProgrammaticName; enabled=$taskCurrent.IsEnabled; offscreen=$taskCurrent.IsOffscreen }
    }
  }
  $taskResult += @{ title=$taskName; processId=$taskWindow.Current.ProcessId; totalElements=$taskElements.Count; items=$taskItems }
}
ConvertTo-Json -InputObject @($taskResult) -Depth 5 -Compress
