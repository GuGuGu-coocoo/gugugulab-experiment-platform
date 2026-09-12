# Isolated synthetic Compose instance

This is a local engineering setup, not a production deployment. Use Docker Engine and Compose v2. The verified environment used Linux arm64, Docker 29.1.3 and Compose 2.40.3 in a dedicated Lima 2.2.0 VM. No host directories were mounted into that VM. The application uses a named volume, UID 10001, a read-only root filesystem and a writable temporary filesystem.

Build from the repository root:

```sh
docker build -t gep-synthetic:local -f deploy/Dockerfile .
```

The base image is pinned by digest and Python distributions by hashes. `.dockerignore` excludes local databases, credentials, documents, tests and experiment build artifacts from the build context.

Create a **new** synthetic volume. Keep the returned volume name and instance UUID for subsequent starts. Never substitute an existing or production volume in the ownership command.

```sh
export GEP_VOLUME="gep_synthetic_$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
docker volume create --label gep.purpose=synthetic "$GEP_VOLUME"
docker run --rm --user 0 -v "$GEP_VOLUME:/data" --entrypoint chown gep-synthetic:local 10001:10001 /data
docker run --rm -v "$GEP_VOLUME:/data" --entrypoint python gep-synthetic:local -m gep.initialize --data-dir /data
export GEP_EXPECTED_INSTANCE="$(docker run --rm -v "$GEP_VOLUME:/data" --entrypoint cat gep-synthetic:local /data/instance)"
```

Initialization refuses any nonempty directory, including a failed partial initialization. It generates one synthetic Owner and private credentials; it never overwrites an Owner. An incomplete initialization marker prevents serving. Inspect a failed initialization rather than deleting or repairing its contents automatically.

Retrieve the synthetic login locally without printing it to shared logs:

```sh
mkdir -p local_data
(umask 077; docker run --rm -v "$GEP_VOLUME:/data" --entrypoint cat gep-synthetic:local /data/dev_credentials.json > local_data/compose_credentials.json)
docker compose up -d --build
```

Open `http://admin.localhost:8000/login` and use that private credentials file. The default experiment/API origin is `http://experiment.localhost:8000`. If another local server already occupies port 8000, stop only that known test service or use a Compose override with a matching public API URL. A Lima host port forward must also agree with `GEP_PUBLIC_API`; configure it explicitly rather than changing a previously bound pending queue.

`docker compose restart` retains the named volume. Starting with a mismatched `GEP_EXPECTED_INSTANCE` stops before serving. Retain the database, `secret`, `instance`, packages and private credentials together; do not copy only the database into a new empty instance. There is no automatic volume deletion in this workflow. This is not backup/restore or production governance acceptance.

On 2026-09-12 the actual Compose service accepted a real exported Godot native experiment, stored four unique records and produced an authorized matching JSONL snapshot. Container restart and replacement preserved instance, Owner credentials, build, records and a volume file. Reinitialization was refused; mounting a different initialized volume with the original expected instance terminated the worker (exit 3).
