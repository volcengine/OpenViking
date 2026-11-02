{{- define "openviking-gateway.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "openviking-gateway.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "openviking-gateway.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "openviking-gateway.selectorLabels" -}}
app.kubernetes.io/name: {{ include "openviking-gateway.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "openviking-gateway.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "openviking-gateway.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "openviking-gateway.image" -}}
{{- $tag := default (printf "v%s" .Chart.AppVersion) .Values.image.tag -}}
{{- if eq .Chart.AppVersion "0.0.0-unreleased" -}}
{{- $tag = required "image.tag is required: no release containing this gateway chart's configuration has been published yet" .Values.image.tag -}}
{{- end -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{- define "openviking-gateway.sqlite" -}}
{{- if or (eq .Values.config.storage.kernel "sqlite") (eq .Values.config.storage.management "sqlite") -}}true{{- end -}}
{{- end -}}

{{- define "openviking-gateway.rootKey" -}}
{{- if and .Values.config.encryption.enabled (has .Values.config.encryption.provider (list "local" "volcengine_kms")) -}}true{{- end -}}
{{- end -}}
