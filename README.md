# pretalx-docker

## MautiCon production operations, 21 October 2026

Coolify resource `vwg0cs0oc8wgkcw0o8csko0o`, `cfp.mautic.org`.
No AGENTS.md is present. Release through Git/Coolify only, never SSH/Docker
operations on the production host. The remaining community instructions below
are background, not a permission to run an untested pull/restart.

The canonical runtime is the root `Dockerfile`, extending the vendor
`pretalx/standalone:v2026.2.1` pinned by digest, plus read-only integrity probes.
The former source-build Dockerfile was not used by the Compose deployment.

Important discovery: production actually uses `/data/db.sqlite3`. The former
`PRETALX_DATABASE_*` and `PRETALX_REDIS_LOCATION` variables were ignored by Pretalx.
Do not switch to the existing, unused PostgreSQL container: that would expose an
empty instance. SQLite stays in this release; PostgreSQL and its volume are
retained untouched. A later database migration is a separate task.

The fixed Compose uses explicit SQLite settings and the vendor's `PRETALX_REDIS`
variable. Sessions stay database-backed to avoid a gratuitous session-backend
change. The trusted proxy count is two for Traefik -> nginx -> Pretalx; only nginx
has public routing, no Gunicorn host port is published. Media uploads are served
as attachments with nosniff and non-executable types. The controlled local MWC CSS
and font routes remain separate.

### Backup, rehearsal and release

Operational helpers reuse the existing Mautic maintenance primitives, not a
second secret store or production deployment channel:

```bash
export PYTHONPATH="$HOME/Development/Projekte/pretix-docker-compose/maintenance"
python3 maintenance/export_pretalx.py
python3 maintenance/qa_pretalx.py "$HOME/Backups/mautic-maintenance/<receiver-directory>"
```

The export makes a native SQLite online backup, checks integrity, bundles data
and public files, encrypts with authenticated OpenSSL CMS, transfers only
ciphertext through a random temporary URL and immediately removes that copy.
Private receiver keys are only in Infisical. Local plaintext stays in mode-0700
storage outside Git. This is an upgrade snapshot, not recurring disaster recovery.

QA uses disposable copies in an internal Docker network, with no published ports,
workers, mail or outside calls. It checks public views, authenticated organiser
views, existing proposals, business/authentication fingerprints, media and the
application secret, then proves a full file/database rollback. An upstream schema
migration moves answer ownership from User to SpeakerProfile; comparisons map
back through `SpeakerProfile.user_id` rather than treating changed profile IDs as
lost data. Vendor UID 999 is used, including its mode-0700 application directory.

Release gates:

1. Keep `main` untouched while testing. Create a temporary hold branch with the
   old pinned image, `entrypoint: [sleep, infinity]`, and `maintenance/nginx-hold.conf`.
   Disable only this app's periodic task, then select/deploy that branch via Coolify.
2. Confirm public maintenance 503 and no app workers. Take a NEW encrypted final
   snapshot, including the actual SQLite DB, matching files and application secret.
3. Deploy a matching target hold branch. No automatic startup migrations or mail
   workers may run while held. Run controlled migrations, rebuild and deploy checks;
   compare production fingerprints/media/secret to the final snapshot.
4. Only after these gates pass, fast-forward `main` to the tested release commit,
   select/deploy `main`, restore the periodic task and independently verify runtime,
   health, API, worker and public rendering.
5. Retain both hold branches only for the immediate recovery period, then remove
   them after retention/backup policy is agreed. No permanent parallel service.

Recovery: remain in hold, never run old app code against the migrated database.
Preserve the failed state, then restore the complete matching SQLite database AND
file snapshot from the protected maintenance directory. Remove target-created
SQLite WAL/SHM files while writers are stopped. A full file restore must remove
files created by the failed release as well as restoring originals. Select the
old pinned hold branch and verify the restored state before reopening old code.
Consider all changes since the snapshot; do not silently roll them back.

No synthetic production emails, proposal submissions or schedule publications.

This repository contains a docker-compose setup for a
[pretalx](https://github.com/pretalx/pretalx) installation based on docker.

**⚠️ Please note that this repository is provided by the pretalx community, and not supported by the pretalx team. ⚠️**

**⚠️ The files in this repository may not support the current pretalx version, or may be undocumented, not secure, and/or
outdated. Please make sure you read the files before executing them, and check the
[issues](https://github.com/pretalx/pretalx-docker/issues) for further information. ⚠️**

## Installation with docker-compose

### For testing

* Run ``docker-compose up -d``. After a few minutes the setup should be accessible at http://localhost/orga
* Set up a user and an organizer by running ``docker exec -it pretalx pretalx init``.

### For production

* Edit ``conf/pretalx.cfg`` and fill in your own values (→ [configuration
  documentation](https://docs.pretalx.org/en/latest/administrator/configure.html))
* Edit ``docker-compose.yml`` and change the line to ``ports: - "127.0.0.1:8346:80"`` (if you use nginx). **Change the
  database password.**
* If you don't want to use docker volumes, create directories for the persistent data and make them read-writeable for
  the userid 999 and the groupid 999. Change ``pretalx-redis``, ``pretalx-database``, ``pretalx-data`` and ``pretalx-public`` to the corresponding
  directories you've chosen.
* Configure a reverse-proxy for better security and to handle TLS. Pretalx listens on port 80 in the ``pretalxdocker``
  network. You can find a few words on an nginx configuration at
  ``reverse-proxy-examples/nginx``
* Make sure you serve all requests for the `/static/` and `/media/` paths (when `debug=false`). See [installation](https://docs.pretalx.org/administrator/installation/#step-7-ssl) for more information
* Optional: Some of the Gunicorn parameters can be adjusted via environment variables:
  * To adjust the number of [Gunicorn workers](https://docs.gunicorn.org/en/stable/settings.html#workers), provide
  the container with `GUNICORN_WORKERS` environment variable.
  * `GUNICORN_MAX_REQUESTS` and `GUNICORN_MAX_REQUESTS_JITTER` to configure the requests a worker instance will process before restarting.
  * `GUNICORN_FORWARDED_ALLOW_IPS` lets you specify which IPs to trust (i.e. which reverse proxies' `X-Forwarded-*` headers can be used to infer connection security).
  * `GUNICORN_BIND_ADDR` can be used to change the interface and port that Gunicorn will listen on. Default: `0.0.0.0:80`

  Here's how to set an environment variable [in
  `docker-compose.yml`](https://docs.docker.com/compose/environment-variables/set-environment-variables/)
  or when using [`docker run` command](https://docs.docker.com/engine/reference/run/#env-environment-variables).
* Run ``docker-compose up -d ``. After a few minutes the setup should be accessible under http://yourdomain.com/orga
* Set up a user and an organizer by running ``docker exec -ti pretalx pretalx init``.
* Set up a cronjob for periodic tasks like this ``15,45 * * * * docker exec pretalx-app pretalx runperiodic``

## Updates with docker-compose

* Run ``docker-compose stop``, ``docker-compose pull`` and finally ``docker-compose up -d`` to upgrade to the newest version. 

## Other installations

* Ansible role without docker: https://github.com/pretalx/ansible-pretalx/
* More complex docker based pretalx installation: https://github.com/allmende/pretalx-docker

# For pretalx-docker maintainers

To upgrade the repo to a new version, e.g. `v2025.2.2`, run `just release v2025.2.2`. This should handle the submodule,
make a properly tagged commit, and push it including the tags.
