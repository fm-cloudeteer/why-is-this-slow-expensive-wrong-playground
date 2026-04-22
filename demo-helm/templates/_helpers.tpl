{{/*
Expand the name of the chart.
*/}}
{{- define "ai-obs-demo.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "ai-obs-demo.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "ai-obs-demo.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: {{ .Values.global.partOf }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{/*
Selector labels for a given component.
Usage: {{ include "ai-obs-demo.selectorLabels" (dict "name" "demo-app" "root" .) }}
*/}}
{{- define "ai-obs-demo.selectorLabels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end }}

{{/*
Langfuse secret name — use existing secret if set, otherwise the chart-managed one.
*/}}
{{- define "ai-obs-demo.langfuseSecretName" -}}
{{- if .Values.demoApp.langfuseExistingSecret }}
{{- .Values.demoApp.langfuseExistingSecret }}
{{- else }}
{{- include "ai-obs-demo.fullname" . }}-langfuse-credentials
{{- end }}
{{- end }}

{{/*
Tenant secret name.
*/}}
{{- define "ai-obs-demo.tenantSecretName" -}}
{{- include "ai-obs-demo.fullname" . }}-tenant-keys
{{- end }}
