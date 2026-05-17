"""Production entrypoint: serve the app on :443 (HTTPS) and redirect :80 -> :443.

Run from the `backend/` folder:

    python -m app.serve

Environment overrides:
    VME_HTTP_PORT     (default 80)
    VME_HTTPS_PORT    (default 443)
    VME_BIND_HOST     (default 0.0.0.0)
    VME_CERT_FILE     (default <project>/certs/cert.pem)
    VME_KEY_FILE      (default <project>/certs/key.pem)

Both ports require elevated privileges on Windows. Install as a service via
`scripts/install-service.ps1` (NSSM).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from hypercorn.asyncio import serve
from hypercorn.config import Config

from .config import CERT_DIR, LOG_ROOT
from .main import app
from .redirect import http_to_https_app

logger = logging.getLogger(__name__)


def _config_https() -> Config:
    cfg = Config()
    cfg.bind = [f"{os.environ.get('VME_BIND_HOST', '0.0.0.0')}:{os.environ.get('VME_HTTPS_PORT', '443')}"]
    cfg.certfile = os.environ.get("VME_CERT_FILE", str(CERT_DIR / "cert.pem"))
    cfg.keyfile = os.environ.get("VME_KEY_FILE", str(CERT_DIR / "key.pem"))
    cfg.alpn_protocols = ["h2", "http/1.1"]
    cfg.accesslog = "-"
    cfg.errorlog = "-"
    cfg.loglevel = "INFO"
    cfg.application_path = "app.main:app"
    return cfg


def _config_http() -> Config:
    cfg = Config()
    cfg.bind = [f"{os.environ.get('VME_BIND_HOST', '0.0.0.0')}:{os.environ.get('VME_HTTP_PORT', '80')}"]
    cfg.accesslog = "-"
    cfg.errorlog = "-"
    cfg.loglevel = "WARNING"
    cfg.application_path = "app.redirect:http_to_https_app"
    return cfg


async def _run() -> None:
    https_cfg = _config_https()
    http_cfg = _config_http()

    cert = Path(https_cfg.certfile or "")
    key = Path(https_cfg.keyfile or "")
    if not cert.is_file() or not key.is_file():
        sys.stderr.write(
            f"\n[ERROR] Cert/key not found.\n"
            f"        cert: {cert}\n"
            f"        key:  {key}\n"
            f"        Run scripts/generate-cert.ps1 first.\n\n"
        )
        sys.exit(2)

    logger.info("Starting HTTPS on %s", https_cfg.bind)
    logger.info("Starting HTTP redirect on %s", http_cfg.bind)

    await asyncio.gather(
        serve(app, https_cfg),
        serve(http_to_https_app, http_cfg),
    )


def main() -> None:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
