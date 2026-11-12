$ErrorActionPreference = "SilentlyContinue"
$out = "data\acceptance\netwatch.csv"
$end = (Get-Date).AddSeconds(600)
"timestamp,pid,tcp_count,udp_count,remotes" | Out-File -Encoding utf8 $out
$seen = 0
while ((Get-Date) -lt $end) {
  $procs = Get-Process -Name ffmpeg
  if (-not $procs) {
    Start-Sleep -Milliseconds 150
    if ((Get-Date) -gt $end.AddSeconds(-1)) { break }
    continue
  }
  foreach ($p in $procs) {
    $tcp = @(Get-NetTCPConnection -OwningProcess $p.Id)
    $udp = @(Get-NetUDPEndpoint -OwningProcess $p.Id)
    $rem = ($tcp | ForEach-Object { "$($_.RemoteAddress):$($_.RemotePort)" }) -join ";"
    "$((Get-Date).ToString('o')),$($p.Id),$($tcp.Count),$($udp.Count),$rem" | Out-File -Append -Encoding utf8 $out
    $seen++
    Start-Sleep -Milliseconds 100
  }
}
"TOTAL_SAMPLES=$seen" | Out-File -Append -Encoding utf8 $out
