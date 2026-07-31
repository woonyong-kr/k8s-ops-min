{{- define "opsia.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "opsia.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else if eq .Release.Name (include "opsia.name" .) -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "opsia.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "opsia.labels" -}}
app.kubernetes.io/name: {{ include "opsia.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "opsia.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opsia.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "opsia.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end -}}

{{- define "opsia.consoleImage" -}}
{{- printf "%s:%s" .Values.console.image.repository (.Values.console.image.tag | default .Chart.AppVersion) -}}
{{- end -}}

{{- define "opsia.accessMode" -}}
{{- $requested := .Values.access.mode -}}
{{- if ne $requested "auto" -}}
{{- $requested -}}
{{- else if eq .Values.service.type "LoadBalancer" -}}
loadbalancer
{{- else if eq .Values.service.type "NodePort" -}}
nodeport
{{- else -}}
{{- $ingressClasses := (lookup "networking.k8s.io/v1" "IngressClass" "" "") | default dict -}}
{{- $ingressItems := (get $ingressClasses "items") | default list -}}
{{- if and .Values.access.host (gt (len $ingressItems) 0) -}}
ingress
{{- else -}}
{{- $nodes := (lookup "v1" "Node" "" "") | default dict -}}
{{- $nodeItems := (get $nodes "items") | default list -}}
{{- $cloud := false -}}
{{- range $node := $nodeItems -}}
{{- $providerID := dig "spec" "providerID" "" $node -}}
{{- if regexMatch "^(aws|gce|azure)://" $providerID -}}
{{- $cloud = true -}}
{{- end -}}
{{- end -}}
{{- if $cloud -}}loadbalancer{{- else -}}portforward{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "opsia.serviceType" -}}
{{- $mode := include "opsia.accessMode" . | trim -}}
{{- if eq $mode "loadbalancer" -}}LoadBalancer
{{- else if eq $mode "nodeport" -}}NodePort
{{- else -}}ClusterIP
{{- end -}}
{{- end -}}

{{- define "opsia.externalUrl" -}}
{{- $configured := .Values.access.externalUrl | trim | trimSuffix "/" -}}
{{- if $configured -}}
{{- $configured -}}
{{- else if and (eq (include "opsia.accessMode" . | trim) "ingress") .Values.access.host -}}
{{- if .Values.access.ingress.tls.enabled -}}https{{- else -}}http{{- end -}}://{{ .Values.access.host }}
{{- end -}}
{{- end -}}

{{- define "opsia.internalUrl" -}}
http://{{ include "opsia.fullname" . }}.{{ .Release.Namespace }}.svc
{{- end -}}

{{- define "opsia.managementBaseUrl" -}}
{{- include "opsia.externalUrl" . | trim | default (include "opsia.internalUrl" . | trim) -}}
{{- end -}}

{{- define "opsia.cookieSecure" -}}
{{- if hasPrefix "https://" (include "opsia.externalUrl" . | trim) -}}1{{- else -}}0{{- end -}}
{{- end -}}

{{- define "opsia.validateAccess" -}}
{{- $mode := include "opsia.accessMode" . | trim -}}
{{- $externalUrl := include "opsia.externalUrl" . | trim -}}
{{- if and (eq $mode "loadbalancer") (hasPrefix "https://" $externalUrl) (ne .Values.access.loadBalancer.tlsTermination "external") -}}
{{- fail "HTTPS load-balancer access requires access.loadBalancer.tlsTermination=external to acknowledge external TLS termination" -}}
{{- end -}}
{{- end -}}
