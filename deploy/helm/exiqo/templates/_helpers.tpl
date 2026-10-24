{{/*
Names are fixed ("exiqo-<part>"): Workload Identity federated credentials name the service
accounts, and the Neo4j subchart's values name exiqo-neo4j, so they cannot follow the release.
*/}}
{{- define "exiqo.labels" -}}
app.kubernetes.io/name: exiqo
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* Selector labels of one component: (dict "ctx" $ "component" "api") */}}
{{- define "exiqo.selector" -}}
app.kubernetes.io/name: exiqo
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "exiqo.componentLabels" -}}
{{ include "exiqo.labels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "exiqo.image" -}}
{{- $img := index .ctx.Values.image .which -}}
{{- if .ctx.Values.image.registry -}}
{{ printf "%s/%s@%s" .ctx.Values.image.registry $img.repository $img.digest }}
{{- else -}}
{{ printf "%s@%s" $img.repository $img.digest }}
{{- end -}}
{{- end -}}

{{- define "exiqo.host" -}}
{{- required "ingress.host is required" .Values.ingress.host -}}
{{- end -}}

{{/* Pod-level hardening for the exiqo images (UID 10001). */}}
{{- define "exiqo.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
fsGroupChangePolicy: OnRootMismatch
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Container-level hardening; (dict "uid" 10001) or empty for 10001. */}}
{{- define "exiqo.containerSecurityContext" -}}
{{- $uid := default 10001 .uid -}}
runAsNonRoot: true
runAsUser: {{ $uid }}
runAsGroup: {{ $uid }}
readOnlyRootFilesystem: true
allowPrivilegeEscalation: false
privileged: false
capabilities:
  drop: [ALL]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "exiqo.imagePullSecrets" -}}
{{- with .Values.image.pullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
The platform secrets: logical key (keyVault.secrets), the file name under /mnt/secrets (the CSI
objectAlias, or the CI Secret's key) and the variable the app reads it from (<NAME>_FILE).
neo4jAuth is not here: only the Neo4j server reads it, from the Secret the CSI driver syncs.
"roles" limits a secret to those pods: the worker runs generated code (dotnet test, terraform) as
its own UID, so it gets only what its jobs use; sign-in secrets stay on the api.
*/}}
{{- define "exiqo.secretTable" -}}
- {key: databaseUrl, file: database_url, env: DATABASE_URL}
- {key: testDatabaseUrl, file: test_database_url, env: TEST_DATABASE_URL}
- {key: appSecret, file: app_secret, env: APP_SECRET}
- {key: sessionSecret, file: session_secret, env: SESSION_SECRET, roles: [api]}
- {key: internalToken, file: internal_token, env: INTERNAL_TOKEN}
- {key: neo4jPassword, file: neo4j_password, env: NEO4J_PASSWORD}
- {key: oidcClientSecret, file: oidc_client_secret, env: OIDC_CLIENT_SECRET, roles: [api]}
- {key: azureOpenaiApiKey, file: azure_openai_api_key, env: AZURE_OPENAI_API_KEY}
- {key: claudeCodeOauthToken, file: claude_code_oauth_token, env: CLAUDE_CODE_OAUTH_TOKEN, when: claude}
- {key: langfuseSecretKey, file: langfuse_secret_key, env: LANGFUSE_SECRET_KEY, when: langfuse}
{{- end -}}

{{/*
The secrets this install gives one role, as a YAML list of the table's rows plus "object":
(dict "ctx" $ "role" "api"|"worker"). A row without "roles" goes to both.
*/}}
{{- define "exiqo.platformSecrets" -}}
{{- $v := .ctx.Values -}}
{{- $role := required "exiqo.platformSecrets needs a role" .role -}}
{{- $rows := list -}}
{{- range (include "exiqo.secretTable" .ctx | fromYamlArray) -}}
{{- $object := index $v.keyVault.secrets .key | default "" -}}
{{- $on := true -}}
{{- if eq (.when | default "") "claude" }}{{ $on = $v.providers.claude.enabled }}{{ end -}}
{{- if eq (.when | default "") "langfuse" }}{{ $on = $v.langfuse.enabled }}{{ end -}}
{{- if and .roles (not (has $role .roles)) }}{{ $on = false }}{{ end -}}
{{- if and $on $object -}}
{{- $rows = append $rows (merge (dict "object" $object) .) -}}
{{- end -}}
{{- end -}}
{{- toYaml $rows -}}
{{- end -}}

{{/* The SecretProviderClass of a role's platform secrets: exiqo-platform (api), exiqo-platform-worker. */}}
{{- define "exiqo.platformClass" -}}
{{- if eq . "api" }}exiqo-platform{{ else }}exiqo-platform-{{ . }}{{ end -}}
{{- end -}}

{{/*
<NAME>_FILE entries for one role's secrets, (dict "ctx" $ "role" "worker"), optionally narrowed
with "only" (list "databaseUrl").
*/}}
{{- define "exiqo.secretFileEnv" -}}
{{- $only := .only | default list -}}
{{- range (include "exiqo.platformSecrets" (dict "ctx" .ctx "role" .role) | fromYamlArray) }}
{{- if or (not $only) (has .key $only) }}
- name: {{ .env }}_FILE
  value: /mnt/secrets/{{ .file }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
The volume behind /mnt/secrets: (dict "ctx" $ "class" "exiqo-platform" "files" (list "database_url")).
With keyVault.mode kubernetesSecret only "files" are projected from the CI Secret, as the class
would mount only its objects.
*/}}
{{- define "exiqo.secretsVolume" -}}
- name: secrets
{{- if eq .ctx.Values.keyVault.mode "kubernetesSecret" }}
  secret:
    secretName: {{ required "keyVault.kubernetesSecretName is required with keyVault.mode kubernetesSecret" .ctx.Values.keyVault.kubernetesSecretName }}
    defaultMode: 0440
    items:
{{- range .files }}
      - {key: {{ . }}, path: {{ . }}}
{{- end }}
{{- else }}
  csi:
    driver: secrets-store.csi.k8s.io
    readOnly: true
    volumeAttributes:
      secretProviderClass: {{ .class }}
{{- end }}
{{- end -}}

{{/* SecretProviderClass parameters: (dict "ctx" $ "objects" (list (dict "object" .. "file" ..))) */}}
{{- define "exiqo.spcParameters" -}}
usePodIdentity: "false"
useVMManagedIdentity: "false"
clientID: {{ required "identity.platformClientId is required" .ctx.Values.identity.platformClientId | quote }}
keyvaultName: {{ required "keyVault.name is required" .ctx.Values.keyVault.name | quote }}
tenantId: {{ .ctx.Values.keyVault.tenantId | default .ctx.Values.auth.tenantId | required "keyVault.tenantId or auth.tenantId is required" | quote }}
objects: |
  array:
{{- range .objects }}
    - |
      objectName: {{ .object }}
      objectAlias: {{ .file }}
      objectType: secret
{{- end }}
{{- end -}}

{{/*
ciEnv (CI overlay only, see values.yaml): env entries that override the ConfigMap's. Closed list,
as the schema's: nothing here may touch sign-in, deploy credentials or a secret (<NAME>_FILE),
which would otherwise win over the entries above it.
*/}}
{{- define "exiqo.ciEnv" -}}
{{- $allowed := list "PROVIDER" "EMBEDDINGS_BACKEND" "BUILD_TOOLCHAIN" "JOB_STALE_SECONDS" -}}
{{- range $name, $value := .Values.ciEnv }}
{{- if not (has $name $allowed) }}
{{- fail (printf "ciEnv.%s is not allowed: ciEnv may set only %s" $name (join ", " $allowed)) }}
{{- end }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- end -}}

{{- define "exiqo.neo4jUri" -}}
{{- if .Values.neo4j.enabled -}}
{{ .Values.neo4j.uri | default "bolt://exiqo-neo4j:7687" }}
{{- else -}}
{{ required "neo4j.uri is required when neo4j.enabled is false" .Values.neo4j.uri }}
{{- end -}}
{{- end -}}

{{/* /tmp, DATA_DIR, the secrets and (when set) the Terraform CLI config: api and worker. */}}
{{- define "exiqo.platformVolumeMounts" -}}
- name: tmp
  mountPath: /tmp
- name: data
  mountPath: /data
- name: secrets
  mountPath: /mnt/secrets
  readOnly: true
{{- if .Values.mirrors.terraformConfig }}
- name: terraform-config
  mountPath: /etc/exiqo
  readOnly: true
{{- end }}
{{- end -}}

{{/* The api and worker pod volumes: (dict "ctx" $ "role" "api" "tmpSize" "1Gi") */}}
{{- define "exiqo.platformVolumes" -}}
- name: tmp
  emptyDir:
    sizeLimit: {{ .tmpSize }}
- name: data
  persistentVolumeClaim:
    claimName: {{ .ctx.Values.data.existingClaim | default "exiqo-data" }}
{{- $files := list }}
{{- range (include "exiqo.platformSecrets" (dict "ctx" .ctx "role" .role) | fromYamlArray) }}{{ $files = append $files .file }}{{ end }}
{{ include "exiqo.secretsVolume" (dict "ctx" .ctx "class" (include "exiqo.platformClass" .role) "files" $files) }}
{{- if .ctx.Values.mirrors.terraformConfig }}
- name: terraform-config
  configMap:
    name: exiqo-terraform
    defaultMode: 0444
{{- end }}
{{- end -}}

{{/* Spread replicas across nodes and zones when the cluster has room (never blocks scheduling). */}}
{{- define "exiqo.spread" -}}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        {{- include "exiqo.selector" . | nindent 8 }}
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        {{- include "exiqo.selector" . | nindent 8 }}
{{- end -}}

{{/* Neo4j backup: the Kubernetes API from bash (curl, the pod's service account token). */}}
{{- define "exiqo.backupKube" -}}
SA=/var/run/secrets/kubernetes.io/serviceaccount
API=https://kubernetes.default.svc
NS=$(cat "$SA/namespace")
STS=exiqo-neo4j
kube() { curl -sS -o /dev/null -w '%{http_code}' --cacert "$SA/ca.crt" -H "Authorization: Bearer $(cat "$SA/token")" "$@"; }
scale() {
  code=$(kube -X PATCH -H 'Content-Type: application/merge-patch+json' --data "{\"spec\":{\"replicas\":$1}}" "$API/apis/apps/v1/namespaces/$NS/statefulsets/$STS/scale")
  [ "$code" = 200 ] || { echo "backup: scaling $STS to $1 failed (HTTP $code)" >&2; return 1; }
}
{{- end -}}

{{- define "exiqo.backupEnv" -}}
- {name: STOP_TIMEOUT, value: {{ .Values.neo4jBackup.stopTimeoutSeconds | quote }}}
- {name: STORAGE_ACCOUNT, value: {{ required "neo4jBackup.storageAccount is required with neo4jBackup.enabled" .Values.neo4jBackup.storageAccount | quote }}}
- {name: CONTAINER, value: {{ .Values.neo4jBackup.container | quote }}}
- {name: HOME, value: /tmp}
- {name: AZURE_CONFIG_DIR, value: /tmp/.azure}
- {name: AZURE_CORE_COLLECT_TELEMETRY, value: "false"}
- {name: AZURE_CORE_ONLY_SHOW_ERRORS, value: "true"}
{{- end -}}
