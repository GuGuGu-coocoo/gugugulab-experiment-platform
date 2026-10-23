# GEP Docker deployment

[English guide](../docs/deployment_en.md) · [中文教程](../docs/deployment.md)

The release uses a Linux amd64 Docker image. On Windows, start Docker Desktop and double-click `start_windows.cmd` in this directory. On Linux, run `sudo sh deploy/linux.sh start` from the release directory.

Open `http://admin.localhost:8080/login` locally, or through an SSH tunnel for a remote server. Create your Owner account in the first-run page. The optional domain is saved but not enabled automatically.

`compose.yaml` is the local, loopback-only configuration. `compose.public.yaml` is an alternative complete configuration for an initialized instance behind Caddy HTTPS; do not combine the two files. Follow the deployment guide before switching. Keep the original `.env` and data volume when upgrading. Stop does not delete data; do not use `down -v` or volume pruning as an upgrade step.

For an existing deployment, use the [0.1.1 upgrade guide](../docs/upgrade_0_1_1_en.md) / [中文升级说明](../docs/upgrade_0_1_1.md), including the explicit database migration. Do not start a fresh instance to upgrade an existing one.
