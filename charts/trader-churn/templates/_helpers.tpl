{{/* Name shared by all resources. */}}
{{- define "trader-churn.fullname" -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" }}
{{- end }}

{{/* Labels on every resource, so kubectl and other tools can find them. */}}
{{- define "trader-churn.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/* Labels that identify the API pods. These must never change after install. */}}
{{- define "trader-churn.apiSelectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: api
{{- end }}

{{/* Security settings for every pod: run as our non-root user. */}}
{{- define "trader-churn.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 1000
runAsGroup: 1000
fsGroup: 1000
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/* Security settings for every container: the "restricted" standard, read-only. */}}
{{- define "trader-churn.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop:
    - ALL
{{- end }}

{{/* Full image names. */}}
{{- define "trader-churn.apiImage" -}}
{{ .Values.api.image.repository }}:{{ .Values.api.image.tag | default .Chart.AppVersion }}
{{- end }}

{{- define "trader-churn.trainingImage" -}}
{{ .Values.training.image.repository }}:{{ .Values.training.image.tag | default .Chart.AppVersion }}
{{- end }}

{{/* How pods reach the model bucket. */}}
{{- define "trader-churn.modelStoreEnv" -}}
- name: AWS_DEFAULT_REGION
  value: {{ .Values.modelStore.region | quote }}
{{- with .Values.modelStore.endpoint }}
- name: AWS_ENDPOINT_URL
  value: {{ . | quote }}
{{- end }}
{{- with .Values.modelStore.credentialsSecret }}
- name: AWS_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: AWS_ACCESS_KEY_ID
- name: AWS_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: AWS_SECRET_ACCESS_KEY
{{- end }}
{{- end }}

{{/* The only outgoing connections allowed: to the model bucket. */}}
{{- define "trader-churn.modelStoreEgress" -}}
{{- if .Values.modelStore.egressCidr }}
- to:
    - ipBlock:
        cidr: {{ .Values.modelStore.egressCidr }}
  ports:
    - protocol: TCP
      port: {{ .Values.modelStore.egressPort }}
{{- else }}
- ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
    - protocol: TCP
      port: 443
{{- end }}
{{- end }}
