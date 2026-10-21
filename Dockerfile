# Vendor release plus read-only operational probes. No runtime dependency installs.
FROM pretalx/standalone:v2025.2.2@sha256:59f7b0bc6482b596eccbe52f8e2a124e5a31e043cef8fc87cc84a6309dfed099
COPY maintenance/qa_pretalx.py maintenance/runtime_probe.py /usr/local/lib/mwc-maintenance/
