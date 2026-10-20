# Deploy GEP on your server

English · [中文](deployment.md)

This guide uses the GEP 0.1.0 Docker release. Start locally, or use a VPS or your own server. The Linux instructions target a fresh Ubuntu 24.04/26.04 LTS x86_64 server. The supplied image is linux/amd64, not ARM.

## 1. Prepare the server

Have the server IP, an SSH account and sudo access ready. Check available storage and arrange an independent backup. The project has not established a measured concurrency or minimum-memory guarantee; size the server for your experiment packages and observed workload.

From a terminal on your computer:

```sh
ssh your_user@your_server_ip
```

Replace the account and IP. Verify the SSH host fingerprint against the provider's console on the first connection; investigate changes rather than ignoring them. If the machine already runs Docker, websites or other services, check existing data and occupied ports first. Do not overwrite their configuration or remove shared components.

You can initialize GEP without a domain through SSH. For public access, use a domain you control. A domain name does not make a private server behind a router or carrier NAT reachable automatically.

## 2. Install Docker

### Ubuntu server

If Docker is already installed, check `sudo docker version` and `sudo docker compose version` instead of reinstalling. On a fresh Ubuntu server, configure the official repository:

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl unzip
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
  "${UBUNTU_CODENAME:-$VERSION_CODENAME}" "$(dpkg --print-architecture)" \
  | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

The final commands should show a client, server and Compose version. For other systems, follow the matching [official Docker instructions](https://docs.docker.com/engine/install/); do not add Ubuntu repositories to a different distribution. These instructions use sudo rather than requiring membership in the privileged docker group.

### Local Windows installation

Install and open [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/). Read and decide whether to accept its agreement, then start the Linux container engine. Download and extract the complete GEP release and double-click `deploy/start_windows.cmd`. Continue with section 4. Python, Node and Godot are not server requirements.

## 3. Download and start GEP

On Linux, create a deployment directory owned by your account:

```sh
mkdir -p gep
cd gep
curl -fL -o gep_0_1_0_docker_linux_amd64.zip \
  https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/releases/download/v0.1.0/gep_0_1_0_docker_linux_amd64.zip
unzip gep_0_1_0_docker_linux_amd64.zip
cd gep_0_1_0_docker_linux_amd64
sudo sh deploy/linux.sh start
```

The launcher loads the bundled image, selects a new persistent volume and starts the first-run page. `.env` records the volume and expected instance UUID, not the Owner password. A healthy service means the page is reachable; no Owner or study exists yet.

For later operation, use the same directory:

```sh
sudo sh deploy/linux.sh status
sudo sh deploy/linux.sh stop
sudo sh deploy/linux.sh start
```

Keep `.env` and the data volume. Diagnose startup errors before changing either; deleting the volume is not a restart procedure.

## 4. Create the Owner account

On the deployment computer, open `http://admin.localhost:8080/login`. For a VPS or another server, open a separate terminal on **your own computer**:

```sh
ssh -N -L localhost:8080:127.0.0.1:8080 your_user@your_server_ip
```

Leave the tunnel running. In Chrome on your computer, open `http://admin.localhost:8080/login`. Resolve any local port 8080 conflict first; do not reuse an unidentified service.

The first-run page asks for an Owner username, a password and its confirmation. The password needs at least six characters, including an ASCII uppercase letter, lowercase letter, digit and visible punctuation; a space does not count as punctuation. The domain is optional: leave it blank for local use, or enter a base domain such as `example.com`, without a scheme, port or path.

Click **完成设置** (Finish setup). After **设置完成** (Setup complete), enter the workbench and log in with your chosen account. The password is stored only as an authentication hash; no plaintext initial-password file is created. Keep your credentials privately. The Owner manages platform accounts and grants access.

The local participant portal is `http://experiment.localhost:8080/`. Localhost means the visitor's own computer; it is not a link for remote participants. Once setup finishes, the setup page closes and a restart does not create another Owner.

## 5. Optional domain and HTTPS

For local use, you can skip this section. Entering a domain in the wizard saves pending configuration only: it does not change DNS or enable public access.

For `example.com`, create DNS records for `admin.example.com` and `experiment.example.com`, both pointing to the server's public address. Add AAAA only if IPv6 is actually configured. Leave the personal website's root/www records alone. Ordinary DNS records are sufficient; GEP does not need your DNS-provider credentials.

Ensure public TCP ports 80 and 443 can reach the server; retain your own SSH access restrictions. Do not expose 8000 or 8080. Docker-published ports may bypass ordinary ufw rules, so check cloud security groups and Docker network rules as well; see [Docker's firewall notes](https://docs.docker.com/engine/network/packet-filtering-firewalls/). Do not replace existing firewall rules for this guide. If a reverse proxy already owns 80/443, route the two hostnames through that proxy instead of stopping an existing website.

In the deployment directory, run `sudoedit .env` and change the existing empty setting. The file is root-owned after an initial sudo launch:

```dotenv
GEP_BASE_DOMAIN=example.com
```

Use your domain and retain the original volume, project name and expected UUID. Switch to the **alternative complete public configuration**:

```sh
sudo docker compose --env-file .env -f compose.yaml stop
sudo docker compose --env-file .env -f compose.public.yaml up -d --wait --wait-timeout 120
sudo docker compose --env-file .env -f compose.public.yaml logs --tail 60 caddy
```

Do not stack the two Compose files. The public configuration exposes only Caddy on 80/443, not the application port. When DNS and network access are ready, Caddy obtains and renews certificates; see [Automatic HTTPS](https://caddyserver.com/docs/automatic-https). Retain its certificate volume too.

Check `https://admin.example.com/login` and `https://experiment.example.com/`. The browser should accept the certificate, and workbench login should succeed. Investigate certificate warnings instead of bypassing them. Log in again on the new domain: local/HTTP session cookies are not transferred.

Before opening participation, run a synthetic study through upload, participation, completion and export. These instructions do not claim that your server or public network has already been verified.

## 6. Start using GEP

Follow the [GEP user guide](researcher_en.md): create a study, set participation and roster, upload and preview the experiment, publish, select its current version and open recruitment. Godot integration is covered in the [GEC developer guide](gec_godot_en.md). A personal website can link to the portal; it should not embed the workbench in a frame.

## 7. Backups and upgrades

The persistent volume holds the database, accounts, secrets, packages and exports. A database-only copy is not a complete backup. For a cold Linux backup, run the following from the deployment directory. This example uses the **public configuration**; substitute `compose.yaml` for local operation:

```sh
umask 077
gep_backup_dir=$(mktemp -d ./backup_XXXXXXXX)
sudo docker compose --env-file .env -f compose.public.yaml stop
sudo docker compose --env-file .env -f compose.public.yaml run --rm -T --no-deps \
  --entrypoint tar gep -C /data -czf - . > "$gep_backup_dir/data.tar.gz"
sudo cat .env > "$gep_backup_dir/config.env"
tar -tzf "$gep_backup_dir/data.tar.gz" >/dev/null
sudo docker compose --env-file .env -f compose.public.yaml up -d --wait
```

Confirm that both backup and archive inspection succeeded, then copy the new `backup_…` directory to independent, protected storage. It contains server secrets; never attach it to public issues. Back up Caddy's certificate volume separately. Each run uses a new directory rather than overwriting the previous backup. Binary redirection above is for a Linux shell, not Windows PowerShell 5.1. On Windows, stop writes and use a backup method suitable for your Docker Desktop version; see the [Docker volume documentation](https://docs.docker.com/engine/storage/volumes/#back-up-restore-or-migrate-data-volumes).

Before upgrading, back up and rehearse on an isolated copy. Load the new image, update the launch files and `GEP_IMAGE` in the **existing deployment directory**, retaining its volume, project and UUID. Recreate containers with the configuration you actually use. Follow version-specific migration notes when applicable. Do not run `down -v`, prune volumes or reinitialize the original volume. An older application must not blindly open a database that has already been migrated.

## 8. Troubleshooting

- **Setup is unreachable:** check Docker, container health, the SSH tunnel and port. Do not substitute a bare server IP for the expected admin hostname.
- **Restart opens login:** the instance is already configured; this is expected.
- **Wrong volume or incomplete initialization:** retain `.env`, volume and logs; check the mount and expected UUID before doing anything else.
- **A saved domain is unreachable:** the wizard does not change DNS or routing. Check the section 5 prerequisites and certificates.
- **Public login or forms are refused:** use the matching HTTPS configuration. Do not disable CSRF or trust client-supplied forwarding headers indiscriminately; see [security notes](security.md).

Publishing 0.1.0 does not establish research approval, consent, retention policy, capacity or scientific timing accuracy. Existing engineering checks are described in [verification](verification.md).
