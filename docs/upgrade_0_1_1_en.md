# Upgrade to GEP 0.1.1

English · [中文](upgrade_0_1_1.md)

For Docker 0.1.0 or an earlier security-candidate installation. For a new instance, use the [deployment guide](deployment_en.md). The 0.1.0 tag and attachment remain unchanged.

## Before upgrading

Arrange a maintenance window. Let active tasks finish and retain their original devices, browser storage and pending files. An upgrade does not move an unfinished task to a new origin. Rehearse on a new synthetic volume first; do not submit real data to public tests or third-party services.

- Keep the original deployment directory, `.env`, `GEP_PROJECT_NAME`, `GEP_VOLUME`, `GEP_EXPECTED_INSTANCE`, port, API address and accounts. Running the launcher in the newly extracted directory creates another instance; it is not an upgrade.
- JSON Schema `pattern` and `patternProperties` are temporarily rejected. Affected old releases cannot admit new participants, and applicable event uploads return `schema_regex_unsupported`. Existing data and pending queues are retained. Assess the impact and prepare a new experiment version without regex rules; do not strip fields, bypass validation or relabel old records.
- Public deployments need `*.experiment.<existing-domain>` and the matching new `compose.public.yaml` and `deploy/Caddyfile`. Releases and previews now use separate origins; updating only the image or only the proxy is insufficient. Keep the existing website, admin address and API address. A custom proxy must provide equivalent isolation, not a shared-origin fallback.
- Local use keeps `compose.yaml`, with no domain or certificate requirement. HTTPS users sign in again and refresh old forms; passwords are not reset. New cookie names do not revoke stolen old credentials.

## 1. Download separately

Download and fully extract `gep_0_1_1_docker_linux_amd64.zip` from the [0.1.1 release](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/releases/tag/v0.1.1). Do not overwrite the old `.env`. The images require Linux amd64 containers, including Docker Desktop's Linux engine on Windows. The archive contains both GEP and the patched Caddy image; it does not require pulling `gep:0.1.1` from a registry.

Run the commands below from the **original deployment directory**. Replace `gep_new` / `$new` with the new extraction path. Use `compose.yaml` locally or `compose.public.yaml` for HTTPS; do not stack them. Stop at any failed step and retain evidence rather than continuing or reinitializing.

## 2. Stop and back up the whole volume

### Linux

```sh
set -eu
umask 077
gep_compose=compose.yaml
gep_new=/path/to/gep_0_1_1_docker_linux_amd64
gep_backup=$(mktemp -d ./backup_0_1_1_XXXXXXXX)
sudo docker compose --env-file .env -f "$gep_compose" stop
cp compose*.yaml "$gep_backup/"
cp -R deploy "$gep_backup/"
sudo cat .env > "$gep_backup/config.env"
sudo docker compose --env-file .env -f "$gep_compose" run --rm -T --no-deps \
  --entrypoint tar gep -C /data -czf - . > "$gep_backup/data.tar.gz"
tar -tzf "$gep_backup/data.tar.gz" >/dev/null
```

### Windows PowerShell

Do not redirect a binary archive with `>` in PowerShell 5.1. The container writes directly to a new backup directory:

```powershell
$ErrorActionPreference = 'Stop'
function Confirm-Docker { if ($LASTEXITCODE -ne 0) { throw 'Docker failed; stop here and keep existing data.' } }
$compose = 'compose.yaml'
$new = 'C:\path\gep_0_1_1_docker_linux_amd64'
$backup = Join-Path (Get-Location) ('backup_0_1_1_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backup | Out-Null
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl.SetOwner($me)
foreach ($sid in @($me, (New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')))) {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $backup -AclObject $acl
docker compose --env-file .env -f $compose stop; Confirm-Docker
Copy-Item .env, compose*.yaml -Destination $backup
Copy-Item deploy -Destination $backup -Recurse
docker compose --env-file .env -f $compose run --rm --no-deps -v "${backup}:/backup" --entrypoint tar gep -czf /backup/data.tar.gz -C /data .; Confirm-Docker
docker compose --env-file .env -f $compose run --rm --no-deps -v "${backup}:/backup:ro" --entrypoint tar gep -tzf /backup/data.tar.gz | Out-Null
Confirm-Docker
```

On either platform, copy the verified backup to independent, protected storage. The volume contains the database, accounts, instance secret, packages and exports; a SQLite-only copy is incomplete. Never attach it to an issue. Public deployments must also back up the original project's complete Caddy `/data` and `/config` volumes before continuing. A readable archive is not proof of restoration; rehearse restoring it to another new volume before operational use.

## 3. Load images and update the original deployment

Linux:

```sh
sudo docker image load --input "$gep_new/gep-image.tar"
cp "$gep_new/compose.yaml" "$gep_new/compose.public.yaml" .
cp "$gep_new/deploy/Caddyfile" "$gep_new/deploy/linux.sh" "$gep_new/deploy/windows.ps1" "$gep_new/deploy/start_windows.cmd" deploy/
cp "$gep_new/docker-release.json" .
sudoedit .env
```

Windows PowerShell:

```powershell
docker image load --input "$new\gep-image.tar"; Confirm-Docker
Copy-Item "$new\compose.yaml", "$new\compose.public.yaml", "$new\docker-release.json" -Destination .
Copy-Item "$new\deploy\Caddyfile", "$new\deploy\linux.sh", "$new\deploy\windows.ps1", "$new\deploy\start_windows.cmd" -Destination deploy
notepad .env
```

Review and merge custom Compose/proxy changes instead of overwriting them. In the original `.env`, change or add only these settings and keep everything else:

```dotenv
GEP_IMAGE=gep:0.1.1
GEP_CADDY_IMAGE=gep-caddy:2.11.7-go1.26.10
GEP_SETUP_ENABLED=0
```

Save the editor before continuing. Do not replace `.env` with `.env.example` or change the volume, instance UUID, project name or domain.

## 4. Explicitly migrate the existing database

This single-line command works in Linux shells and PowerShell. Replace `compose.yaml` with the configuration selected in step 2:

```sh
docker compose --env-file .env -f compose.yaml run --rm --no-deps gep python -c "from gep.runtime import configure, verify; configure(); import django; django.setup(); verify(); from django.core.management import call_command; call_command('migrate', interactive=False)"
```

Prefix with `sudo` where Linux requires Docker privileges. In PowerShell, run `Confirm-Docker` immediately afterwards. A successful upgrade includes `0016_browser_origin`; an existing security candidate may report no pending migrations. Application startup does not migrate for you.

The command verifies the volume marker and database instance. It adds public routing labels and framework default permissions, without recreating the Owner, resetting passwords or rewriting sessions, events or frozen packages. If migration fails, leave the service stopped and inspect the evidence. Do not delete the volume, reinitialize it or let the old application write to the migrated database.

## 5. Start and verify

```sh
docker compose --env-file .env -f compose.yaml up -d --wait --wait-timeout 120
docker compose --env-file .env -f compose.yaml ps
```

Again use the selected configuration, `sudo` where needed and `Confirm-Docker` after each Docker command in PowerShell. Sign in with the original account and check studies, permissions, participation states and existing exports. Use a synthetic study first to check upload, preview, isolated-origin participation, completion and export. For public access, verify exact-origin CORS, certificate chains and admin `__Host-` cookies; do not bypass certificate errors.

The original origin's `/local-data` page retains uploads with their original bindings. It does not migrate data or automatically resume tasks. Keep browser storage, pending records and failure messages. Old caches, Service Workers and potentially leaked credentials need separate handling. Never use `down -v`, volume pruning, database deletion or cache clearing as an upgrade procedure.

For rollback, stop writes and preserve the failed state. Restore the pre-upgrade whole-volume backup and configuration to **another new volume / isolated directory**. Verify accounts, instance identity, permissions, revocation and exports before a maintainer switches service. Do not overwrite a volume that may contain new data or merely set `GEP_IMAGE` back to 0.1.0.

## Scope and open risks

GEC SDK 0.1.0, protocol `gep/1`, scientific tasks, event contents and export encoding are unchanged. Retaining 0.1.0 does not recommend its unpatched attachment. See the [audit](SECURITY_AUDIT.md) for evidence and open findings. Legacy browsers, stolen credentials, password policy, capacity, host security, actual DNS/ACME and existing SDK harness failures remain unresolved. Publication does not mean every test passed, absolute security or approval to collect real participant data.
