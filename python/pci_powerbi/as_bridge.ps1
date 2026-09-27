# Local-only bridge to the Analysis Services engine hosted by ONE Power BI Desktop process (M12).
# Uses the ADOMD client that ships with Power BI Desktop (no extra dependency). Connects to localhost only.
#   -Mode query : run the DAX/DMV text in -In, write a typed TSV to -Out
#   -Mode exec  : run the TMSL/XMLA command in -In (e.g. refresh)
param([Parameter(Mandatory = $true)][int]$DesktopPid,
      [Parameter(Mandatory = $true)][ValidateSet("query", "exec")][string]$Mode,
      [Parameter(Mandatory = $true)][string]$In,
      [string]$Out,
      [int]$TimeoutSec = 600)
$ErrorActionPreference = "Stop"
$pkg = Get-AppxPackage -Name Microsoft.MicrosoftPowerBIDesktop | Select-Object -First 1
if ($pkg) { $bin = Join-Path $pkg.InstallLocation "bin" } else { $bin = "C:\Program Files\Microsoft Power BI Desktop\bin" }
Add-Type -Path (Join-Path $bin "Microsoft.PowerBI.AdomdClient.dll")
$as = Get-CimInstance Win32_Process -Filter "Name='msmdsrv.exe'" | Where-Object { $_.ParentProcessId -eq $DesktopPid } | Select-Object -First 1
if (-not $as) { throw "No Analysis Services engine found for Power BI Desktop PID $DesktopPid" }
$port = (Get-NetTCPConnection -OwningProcess $as.ProcessId -State Listen |
         Where-Object { $_.LocalAddress -in "127.0.0.1", "::1" } | Select-Object -First 1).LocalPort
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port")
$conn.Open()
try {
  $cmd = $conn.CreateCommand()
  $cmd.CommandText = [IO.File]::ReadAllText($In, [Text.Encoding]::UTF8)
  $cmd.CommandTimeout = $TimeoutSec     # the engine cancels the query when exceeded
  if ($Mode -eq "exec") { [void]$cmd.ExecuteNonQuery(); "OK" ; return }
  $inv = [Globalization.CultureInfo]::InvariantCulture
  $sw = [System.IO.StreamWriter]::new($Out, $false, [System.Text.UTF8Encoding]::new($false))
  $r = $cmd.ExecuteReader()
  $names = @(); $types = @()
  for ($i = 0; $i -lt $r.FieldCount; $i++) { $names += $r.GetName($i); $types += $r.GetFieldType($i).Name }
  $sw.WriteLine(($names -join "`t")); $sw.WriteLine(($types -join "`t"))
  $n = 0
  while ($r.Read()) {
    $vals = New-Object string[] $r.FieldCount
    for ($i = 0; $i -lt $r.FieldCount; $i++) {
      $v = $r.GetValue($i)
      if ($v -is [DBNull] -or $null -eq $v) { $vals[$i] = "\N" }
      elseif ($v -is [double]) { $vals[$i] = $v.ToString("R", $inv) }
      elseif ($v -is [decimal] -or $v -is [single]) { $vals[$i] = ([double]$v).ToString("R", $inv) }
      elseif ($v -is [datetime]) { $vals[$i] = $v.ToString("yyyy-MM-dd", $inv) }
      elseif ($v -is [bool]) { $vals[$i] = $(if ($v) { "true" } else { "false" }) }
      else { $vals[$i] = ([string]$v).Replace("`t", " ").Replace("`r", " ").Replace("`n", " ") }
    }
    $sw.WriteLine(($vals -join "`t")); $n++
  }
  $r.Close(); $sw.Close()
  "ROWS $n"
} finally { $conn.Close() }
