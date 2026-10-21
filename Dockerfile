# Vendor release plus read-only operational probes. No runtime dependency installs.
FROM pretalx/standalone:v2026.2.1@sha256:aca4ef20b30452d0941f064e84f8a5a997b34b7af95c939e7e12623d0ce94be7
COPY maintenance/qa_pretalx.py maintenance/runtime_probe.py /usr/local/lib/mwc-maintenance/
