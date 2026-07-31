param(
  [string]$TargetContext = "",
  [string]$TargetNamespace = "target",
  [Parameter(Mandatory = $true)]
  [string]$AssetBaseUrl,
  [string]$ClusterId = "",
  [string]$WorkspaceId = "",
  [string]$ManagementApiBaseUrl = "",
  [string]$AgentToken = ""
)

$ErrorActionPreference = "Stop"
$AlertmanagerConfigSecret = "kyro-alertmanager-config"
$PrometheusSliAlertName = "OpsiaSliFailureRatioHigh"
$AlertmanagerWebhookUrl = ""
$PrometheusDynamicValues = ""

function Require-Command([string]$Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "missing required command: $Name"
  }
}

function Invoke-Native([scriptblock]$Command, [string]$Failure) {
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw $Failure
  }
}

function Wait-ServiceEndpoints([string]$Name) {
  Invoke-Native {
    & kubectl --context $TargetContext -n $TargetNamespace get "service/$Name"
  } "service/$Name is missing"
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $ready = & kubectl --context $TargetContext -n $TargetNamespace get endpointslices `
      -l "kubernetes.io/service-name=$Name" `
      -o "jsonpath={range .items[*].endpoints[*]}{.conditions.ready}{'\n'}{end}" 2>$null
    if (($ready -join "`n") -match "(^|`n)true(`n|$)") {
      return
    }
    Start-Sleep -Seconds 2
  }
  throw "service/$Name has no ready endpoints"
}

function Require-ReleaseWorkload([string]$Release) {
  $resources = & kubectl --context $TargetContext -n $TargetNamespace `
    get deployment,statefulset,daemonset `
    -l "app.kubernetes.io/instance=$Release" -o name 2>$null
  if (-not $resources) {
    throw "telemetry release $Release has no workload"
  }
}

function Require-TempoRuntimeBounds {
  $argsText = (
    & kubectl --context $TargetContext -n $TargetNamespace `
      get statefulset/tempo `
      -o "jsonpath={.spec.template.spec.containers[0].args[*]}"
  ).Trim()
  if ($LASTEXITCODE -ne 0) {
    throw "tempo StatefulSet lookup failed"
  }
  $memoryLimit = (
    & kubectl --context $TargetContext -n $TargetNamespace `
      get statefulset/tempo `
      -o "jsonpath={.spec.template.spec.containers[0].resources.limits.memory}"
  ).Trim()
  if ($LASTEXITCODE -ne 0) {
    throw "tempo memory limit lookup failed"
  }
  $renderedConfig = (
    & kubectl --context $TargetContext -n $TargetNamespace `
      get configmap/tempo -o "jsonpath={.data.tempo\.yaml}"
  ) -join "`n"
  if ($LASTEXITCODE -ne 0) {
    throw "tempo config lookup failed"
  }

  if ($argsText -notmatch "(^|\s)-mem-ballast-size-mbs=0(\s|$)") {
    throw "tempo runtime has an unsafe memory ballast: $argsText"
  }
  if ($memoryLimit -ne "1Gi") {
    throw "tempo runtime memory limit is not the required 1Gi: $memoryLimit"
  }
  foreach ($expected in @(
    "block_retention: 6h",
    "trace_idle_period: 10s",
    "max_block_duration: 5m",
    "max_concurrent_queries: 4",
    "concurrent_jobs: 32"
  )) {
    if (-not $renderedConfig.Contains($expected)) {
      throw "tempo runtime config is missing required bound: $expected"
    }
  }
}

function Protect-TemporaryPath([string]$Path, [string]$Mode) {
  # Windows user temp inherits its per-user ACL. Unix PowerShell needs an
  # explicit mode because this directory can temporarily hold a Bearer token.
  if (Get-Command chmod -ErrorAction SilentlyContinue) {
    & chmod $Mode $Path
    if ($LASTEXITCODE -ne 0) {
      throw "unable to protect temporary telemetry path"
    }
  }
}

function Get-AlertmanagerWebhookUrl {
  $base = $ManagementApiBaseUrl.TrimEnd("/")
  try {
    $uri = [Uri]$base
  }
  catch {
    throw "ManagementApiBaseUrl must be a normalized http(s) URL ending in /api"
  }
  if (
    -not $uri.IsAbsoluteUri -or
    $uri.Scheme -notin @("http", "https") -or
    -not $uri.Host -or
    $uri.UserInfo -or
    $uri.Query -or
    $uri.Fragment -or
    $uri.AbsolutePath -ne "/api"
  ) {
    throw "ManagementApiBaseUrl must be a normalized http(s) URL ending in /api"
  }
  $cluster = [Uri]::EscapeDataString($ClusterId)
  $workspace = [Uri]::EscapeDataString($WorkspaceId)
  return "$base/webhooks/alertmanager?cluster_id=$cluster&workspace_id=$workspace"
}

function Configure-AlertmanagerWebhook([string]$Directory) {
  $configured = @($ClusterId, $WorkspaceId, $ManagementApiBaseUrl, $AgentToken) |
    Where-Object { $_ }
  if ($configured.Count -eq 0) {
    return
  }
  if ($configured.Count -ne 4) {
    throw "Alertmanager webhook requires ClusterId, WorkspaceId, ManagementApiBaseUrl, and AgentToken"
  }

  $script:AlertmanagerWebhookUrl = Get-AlertmanagerWebhookUrl
  $configFile = Join-Path $Directory "alertmanager.json"
  $config = [ordered]@{
    global = [ordered]@{ resolve_timeout = "5m" }
    route = [ordered]@{
      receiver = "kyro-rca"
      group_by = @(
        "alertname",
        "opsia_namespace",
        "opsia_resource_kind",
        "opsia_resource_name",
        "opsia_service",
        "opsia_sli",
        "opsia_symptom"
      )
      group_wait = "5s"
      group_interval = "15s"
      repeat_interval = "5m"
    }
    receivers = @(
      [ordered]@{
        name = "kyro-rca"
        webhook_configs = @(
          [ordered]@{
            url = $script:AlertmanagerWebhookUrl
            send_resolved = $true
            http_config = [ordered]@{
              authorization = [ordered]@{
                type = "Bearer"
                credentials = $AgentToken
              }
            }
          }
        )
      }
    )
  }
  $utf8 = New-Object Text.UTF8Encoding($false)
  [IO.File]::WriteAllText($configFile, ($config | ConvertTo-Json -Depth 12 -Compress), $utf8)
  Protect-TemporaryPath $configFile "600"

  Write-Host "configuring authenticated Alertmanager delivery to the management API"
  Invoke-Native {
    & kubectl --context $TargetContext -n $TargetNamespace `
      create secret generic $AlertmanagerConfigSecret `
      "--from-file=alertmanager.yml=$configFile" `
      --dry-run=client -o yaml |
      & kubectl --context $TargetContext -n $TargetNamespace apply -f -
  } "Alertmanager webhook secret installation failed"

  $script:PrometheusDynamicValues = Join-Path $Directory "prometheus-alertmanager-values.yaml"
  $values = @"
alertmanager:
  config:
    enabled: false
  extraSecretMounts:
    - name: kyro-alertmanager-config
      mountPath: /etc/alertmanager/alertmanager.yml
      subPath: alertmanager.yml
      secretName: $AlertmanagerConfigSecret
      readOnly: true
"@
  [IO.File]::WriteAllText($script:PrometheusDynamicValues, $values, $utf8)
  Protect-TemporaryPath $script:PrometheusDynamicValues "600"
}

function Require-PrometheusSliRuleLoaded {
  $path = (
    "/api/v1/namespaces/$TargetNamespace/services/" +
    "http:prometheus:http/proxy/api/v1/rules"
  )
  for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $raw = & kubectl --context $TargetContext get "--raw=$path" 2>$null
    if ($LASTEXITCODE -eq 0 -and $raw) {
      try {
        $body = ($raw -join "`n") | ConvertFrom-Json
        $requiredQueryParts = @(
          'namespace!=""',
          'resource_kind!=""',
          'resource_name!=""',
          'service!=""',
          'sli!=""',
          'symptom!=""',
          "> 0.2"
        )
        $requiredLabels = @(
          "opsia_namespace",
          "opsia_resource_kind",
          "opsia_resource_name",
          "opsia_service",
          "opsia_sli",
          "opsia_symptom"
        )
        $requiredAnnotations = @(
          "opsia_observed_value",
          "opsia_threshold"
        )
        $allRules = @(
          $body.data.groups |
          ForEach-Object { $_.rules }
        )
        $recordingRules = @(
          $allRules |
          Where-Object { $_.name -eq "opsia_sli_failure_ratio" }
        )
        $recordingRuleLoaded = $false
        foreach ($recordingRule in $recordingRules) {
          $normalizedRecordQuery = ([string]$recordingRule.query) -replace '\s+', ''
          $sixLabelSum = "sumby(namespace,resource_kind,resource_name,service,sli,symptom)"
          if (
            $normalizedRecordQuery.Contains("opsia_sli_requests_total") -and
            $normalizedRecordQuery.Contains('outcome="failure"') -and
            [regex]::Matches(
              $normalizedRecordQuery,
              [regex]::Escape($sixLabelSum)
            ).Count -eq 2 -and
            -not $normalizedRecordQuery.Contains("pod") -and
            -not $normalizedRecordQuery.Contains("instance")
          ) {
            $recordingRuleLoaded = $true
          }
        }
        $alertRules = @(
          $allRules |
          Where-Object {
            $rule = $_
            $rule.name -eq $PrometheusSliAlertName -and
            @(
              $requiredQueryParts |
              Where-Object { -not ([string]$rule.query).Contains($_) }
            ).Count -eq 0 -and
            @(
              $requiredLabels |
              Where-Object {
                $property = $rule.labels.PSObject.Properties[$_]
                $null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)
              }
            ).Count -eq 0 -and
            @(
              $requiredAnnotations |
              Where-Object {
                $property = $rule.annotations.PSObject.Properties[$_]
                $null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)
              }
            ).Count -eq 0
          }
        )
        if (
          $body.status -eq "success" -and
          $recordingRuleLoaded -and
          $alertRules.Count -gt 0
        ) {
          return
        }
      }
      catch {
        # Prometheus may still be starting or reloading its rule files.
      }
    }
    Start-Sleep -Seconds 2
  }
  throw (
    "Prometheus did not load SLI recording rule opsia_sli_failure_ratio " +
    "and alert rule $PrometheusSliAlertName"
  )
}

function Restart-AlertmanagerForWebhookConfig {
  if (-not $script:AlertmanagerWebhookUrl) {
    return
  }
  $statefulsets = & kubectl --context $TargetContext -n $TargetNamespace `
    get statefulset `
    -l "app.kubernetes.io/instance=prometheus,app.kubernetes.io/name=alertmanager" `
    -o name
  if ($LASTEXITCODE -ne 0 -or -not $statefulsets) {
    throw "Alertmanager StatefulSet is missing"
  }
  foreach ($statefulset in @($statefulsets)) {
    # Secret subPath mounts only refresh on Pod recreation. Always restart so
    # a reissued per-cluster token cannot leave Alertmanager using stale auth.
    Invoke-Native {
      & kubectl --context $TargetContext -n $TargetNamespace `
        rollout restart $statefulset
    } "Alertmanager restart failed"
    Invoke-Native {
      & kubectl --context $TargetContext -n $TargetNamespace `
        rollout status $statefulset --timeout=180s
    } "Alertmanager rollout failed"
  }
}

function Require-AlertmanagerWebhookConfig {
  if (-not $script:AlertmanagerWebhookUrl) {
    return
  }

  $secret = & kubectl --context $TargetContext -n $TargetNamespace `
    get secret $AlertmanagerConfigSecret -o json | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0) {
    throw "Alertmanager webhook secret lookup failed"
  }
  $encoded = $secret.data."alertmanager.yml"
  try {
    $decoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
    $config = $decoded | ConvertFrom-Json
    $receiver = @($config.receivers | Where-Object { $_.name -eq "kyro-rca" })
    $hook = @($receiver[0].webhook_configs)[0]
    if (
      $receiver.Count -ne 1 -or
      $hook.url -ne $script:AlertmanagerWebhookUrl -or
      $hook.send_resolved -ne $true -or
      $hook.http_config.authorization.type -ne "Bearer" -or
      $hook.http_config.authorization.credentials -cne $AgentToken
    ) {
      throw "invalid Alertmanager webhook configuration"
    }
  }
  catch {
    throw "Alertmanager webhook configuration verification failed"
  }

  $statefulsets = & kubectl --context $TargetContext -n $TargetNamespace `
    get statefulset `
    -l "app.kubernetes.io/instance=prometheus,app.kubernetes.io/name=alertmanager" `
    -o json | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0) {
    throw "Alertmanager StatefulSet lookup failed"
  }
  $validMount = $false
  foreach ($item in @($statefulsets.items)) {
    $secretVolumes = @(
      $item.spec.template.spec.volumes |
      Where-Object { $_.secret.secretName -eq $AlertmanagerConfigSecret } |
      ForEach-Object { $_.name }
    )
    $configMounts = @(
      $item.spec.template.spec.containers |
      ForEach-Object { $_.volumeMounts } |
      Where-Object { $_.mountPath -eq "/etc/alertmanager/alertmanager.yml" }
    )
    if (
      $configMounts.Count -eq 1 -and
      $configMounts[0].name -in $secretVolumes -and
      $configMounts[0].readOnly -eq $true
    ) {
      $validMount = $true
    }
  }
  if (-not $validMount) {
    throw "Alertmanager webhook secret must be the only read-only config mount"
  }

  $serviceName = (
    & kubectl --context $TargetContext -n $TargetNamespace `
      get service `
      -l "app.kubernetes.io/instance=prometheus,app.kubernetes.io/name=alertmanager" `
      -o 'jsonpath={range .items[?(@.spec.clusterIP!="None")]}{.metadata.name}{"\n"}{end}'
  ).Trim()
  if ($LASTEXITCODE -ne 0 -or -not $serviceName) {
    throw "Alertmanager Service is missing"
  }
  $path = (
    "/api/v1/namespaces/$TargetNamespace/services/" +
    "http:${serviceName}:http/proxy/api/v2/status"
  )
  for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $raw = & kubectl --context $TargetContext get "--raw=$path" 2>$null
    if ($LASTEXITCODE -eq 0 -and $raw) {
      try {
        $status = ($raw -join "`n") | ConvertFrom-Json
        # Alertmanager exposes its active config as redacted YAML. The Secret
        # check above already proves the exact URL/token, so this runtime check
        # only verifies that the authenticated receiver was actually loaded.
        $runtimeConfig = [string]$status.config.original
        $required = @(
          '(?m)^\s*receiver:\s+kyro-rca\s*$',
          '(?m)^\s*-\s+name:\s+kyro-rca\s*$',
          '(?m)^\s*(?:-\s+)?send_resolved:\s+true\s*$',
          '(?m)^\s*authorization:\s*$',
          '(?m)^\s*type:\s+Bearer\s*$',
          '(?m)^\s*credentials:\s+<secret>\s*$',
          '(?m)^\s*url:\s+<secret>\s*$'
        )
        $validRuntime = $true
        foreach ($pattern in $required) {
          if ($runtimeConfig -notmatch $pattern) {
            $validRuntime = $false
            break
          }
        }
        if ($validRuntime) {
          return
        }
      }
      catch {
        # Alertmanager may still be starting after the mandatory token reload.
      }
    }
    Start-Sleep -Seconds 2
  }
  throw "Alertmanager runtime did not load the authenticated webhook config"
}

function New-RandomHex {
  $bytes = New-Object byte[] 32
  $generator = New-Object Security.Cryptography.RNGCryptoServiceProvider
  try {
    $generator.GetBytes($bytes)
  }
  finally {
    $generator.Dispose()
  }
  return ([BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
}

Require-Command "helm"
Require-Command "kubectl"

if (-not $TargetContext) {
  $TargetContext = (& kubectl config current-context).Trim()
}
if (-not $TargetContext) {
  throw "unable to resolve the current kubectl context"
}

$assetDirectory = Join-Path ([IO.Path]::GetTempPath()) ("kyro-telemetry-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $assetDirectory | Out-Null
Protect-TemporaryPath $assetDirectory "700"
try {
  $assets = @("prometheus.yaml", "loki.yaml", "tempo.yaml", "opentelemetry.yaml", "minio.yaml")
  foreach ($asset in $assets) {
    Invoke-WebRequest -UseBasicParsing `
      -Uri ($AssetBaseUrl.TrimEnd("/") + "/" + $asset) `
      -OutFile (Join-Path $assetDirectory $asset)
  }

  Invoke-Native {
    & kubectl --context $TargetContext create namespace $TargetNamespace `
      --dry-run=client -o yaml |
      & kubectl --context $TargetContext apply -f -
  } "target namespace creation failed"

  Configure-AlertmanagerWebhook $assetDirectory

  $minioPassword = ""
  $encodedPassword = & kubectl --context $TargetContext -n $TargetNamespace `
    get secret minio-secret -o "jsonpath={.data.MINIO_ROOT_PASSWORD}" 2>$null
  if ($encodedPassword) {
    $minioPassword = [Text.Encoding]::UTF8.GetString(
      [Convert]::FromBase64String(($encodedPassword -join "").Trim())
    )
  }
  if (-not $minioPassword) {
    $minioPassword = New-RandomHex
  }

  Invoke-Native {
    & kubectl --context $TargetContext -n $TargetNamespace create secret generic minio-secret `
      --from-literal="MINIO_ROOT_USER=minioadmin" `
      --from-literal="MINIO_ROOT_PASSWORD=$minioPassword" `
      --dry-run=client -o yaml |
      & kubectl --context $TargetContext -n $TargetNamespace apply -f -
  } "MinIO credential installation failed"
  Invoke-Native {
    & kubectl --context $TargetContext -n $TargetNamespace `
      delete job/minio-create-buckets --ignore-not-found --wait=true
  } "old MinIO bucket job cleanup failed"
  Invoke-Native {
    & kubectl --context $TargetContext -n $TargetNamespace `
      apply -f (Join-Path $assetDirectory "minio.yaml")
  } "MinIO installation failed"
  Invoke-Native {
    & kubectl --context $TargetContext -n $TargetNamespace `
      rollout status statefulset/minio --timeout=180s
  } "MinIO rollout failed"
  Invoke-Native {
    & kubectl --context $TargetContext -n $TargetNamespace `
      wait --for=condition=complete job/minio-create-buckets --timeout=180s
  } "MinIO bucket creation failed"

  Invoke-Native {
    & helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
  } "Prometheus Helm repository setup failed"
  Invoke-Native {
    & helm repo add grafana https://grafana.github.io/helm-charts --force-update
  } "Grafana Helm repository setup failed"
  Invoke-Native {
    & helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts --force-update
  } "OpenTelemetry Helm repository setup failed"
  Invoke-Native { & helm repo update } "Helm repository update failed"

  $releases = @(
    @("prometheus", "prometheus-community/prometheus", "29.19.0", "prometheus.yaml"),
    @("loki", "grafana/loki", "7.1.0", "loki.yaml"),
    @("tempo", "grafana/tempo", "1.24.4", "tempo.yaml"),
    @("opentelemetry-collector", "open-telemetry/opentelemetry-collector", "0.165.0", "opentelemetry.yaml")
  )
  foreach ($release in $releases) {
    $name, $chart, $version, $values = $release
    $helmArguments = @(
      "upgrade", "--install", $name, $chart,
      "--version", $version,
      "--kube-context", $TargetContext,
      "--namespace", $TargetNamespace,
      "--values", (Join-Path $assetDirectory $values)
    )
    if ($name -eq "prometheus" -and $script:PrometheusDynamicValues) {
      $helmArguments += @("--values", $script:PrometheusDynamicValues)
    }
    $helmArguments += @("--wait", "--timeout", "5m")
    Invoke-Native {
      & helm @helmArguments
    } "$name installation failed"
    Require-ReleaseWorkload $name
    if ($name -eq "prometheus") {
      Restart-AlertmanagerForWebhookConfig
    }
  }

  Require-TempoRuntimeBounds
  Wait-ServiceEndpoints "prometheus"
  Require-PrometheusSliRuleLoaded
  Require-AlertmanagerWebhookConfig
  foreach ($service in @("loki-gateway", "tempo", "opentelemetry-collector")) {
    Wait-ServiceEndpoints $service
  }

  Write-Host "telemetry is installed in context $TargetContext, namespace $TargetNamespace."
}
finally {
  Remove-Item -LiteralPath $assetDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
