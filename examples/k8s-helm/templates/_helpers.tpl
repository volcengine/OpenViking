{{/*
Expand the name of the chart.
*/}}
{{- define "openviking.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "openviking.fullname" -}}
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
Create chart name and version as used by the chart label.
*/}}
{{- define "openviking.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "openviking.labels" -}}
helm.sh/chart: {{ include "openviking.chart" . }}
{{ include "openviking.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "openviking.selectorLabels" -}}
app.kubernetes.io/name: {{ include "openviking.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "openviking.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "openviking.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Get cloud provider specific service annotations
*/}}
{{- define "openviking.serviceAnnotations" -}}
{{- if .Values.cloudProvider }}
{{- $providerConfig := index .Values.cloudProviders .Values.cloudProvider }}
{{- if $providerConfig }}
{{- with $providerConfig.service.annotations }}
{{- toYaml . }}
{{- end }}
{{- end }}
{{- end }}
{{- with .Values.service.annotations }}
{{- toYaml . }}
{{- end }}
{{- end }}
