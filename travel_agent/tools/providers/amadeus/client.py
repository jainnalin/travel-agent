# travel_agent/tools/providers/amadeus/client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
import os
import time
import requests
from urllib.parse import urlencode


@dataclass(frozen=True)
class AmadeusConfig:
    """
    Minimal Amadeus config (no legacy imports).
    Provide values via env vars:
      AMADEUS_CLIENT_ID
      AMADEUS_CLIENT_SECRET
      AMADEUS_BASE_URL (optional; defaults to test)
      AMADEUS_TIMEOUT_S (optional; defaults to 30)
    """
    client_id: str
    client_secret: str
    base_url: str = "https://test.api.amadeus.com"
    timeout_s: int = 30


def load_amadeus_config_from_env() -> AmadeusConfig:
    cid = os.getenv("AMADEUS_CLIENT_ID", "").strip()
    csec = os.getenv("AMADEUS_CLIENT_SECRET", "").strip()
    base = os.getenv("AMADEUS_BASE_URL", "").strip() or "https://test.api.amadeus.com"
    timeout_s = int(os.getenv("AMADEUS_TIMEOUT_S", "30"))

    if not cid or not csec:
        raise RuntimeError("Missing Amadeus credentials. Set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET.")

    return AmadeusConfig(
        client_id=cid,
        client_secret=csec,
        base_url=base,
        timeout_s=timeout_s,
    )


class AmadeusClient:
    """
    Minimal Amadeus HTTP client with OAuth token management.
    IMPORTANT: This client performs *no retries*.
    All retries should be implemented at the agent level.
    """

    def __init__(self, config: AmadeusConfig):
        self.config = config
        self._token: Optional[str] = None
        self._token_expiry_ts: float = 0.0  # epoch seconds

    # --------------------
    # OAuth token
    # --------------------
    def _token_valid(self) -> bool:
        # refresh a bit early to avoid edge-of-expiry failures
        return bool(self._token) and time.time() < (self._token_expiry_ts - 30)

    def _fetch_token(self) -> Tuple[str, float]:
        url = f"{self.config.base_url}/v1/security/oauth2/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }

        r = requests.post(url, data=data, timeout=self.config.timeout_s)
        r.raise_for_status()
        payload = r.json()

        token = payload.get("access_token")
        expires_in = payload.get("expires_in", 0)

        if not isinstance(token, str) or not token.strip():
            raise RuntimeError(f"Amadeus token response missing access_token: {payload}")

        try:
            expires_in_s = int(expires_in)
        except Exception:
            expires_in_s = 0

        expiry_ts = time.time() + max(0, expires_in_s)
        return token, expiry_ts

    def _ensure_token(self) -> str:
        if self._token_valid():
            return self._token  # type: ignore
        token, expiry_ts = self._fetch_token()
        self._token = token
        self._token_expiry_ts = expiry_ts
        return token

    # --------------------
    # Debug helpers
    # --------------------
    def _headers(self) -> Dict[str, str]:
        token = self._ensure_token()
        return {"Authorization": f"Bearer {token}"}

    def _debug_enabled(self) -> bool:
        return os.getenv("AMADEUS_DEBUG_HTTP", "").strip() in ("1", "true", "TRUE", "yes", "YES")

    def _debug_request_summary(self, method: str, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            qs = urlencode({k: str(v) for k, v in (params or {}).items()})
            full_url = f"{url}?{qs}" if qs else url
            return {
                "method": method,
                "url": url,
                "full_url_len": len(full_url),
                "params_keys": sorted(list((params or {}).keys())),
                "hotelIds_len": len(str((params or {}).get("hotelIds", ""))) if params else 0,
                "hotelIds_count": (str((params or {}).get("hotelIds", "")).count(",") + 1)
                if params and params.get("hotelIds") else 0,
            }
        except Exception:
            return {"method": method, "url": url}

    # --------------------
    # Failure injection (for testing agent retries)
    # --------------------
    def _maybe_inject_fail_once(self, method: str, path: str) -> None:
        """
        If AMADEUS_INJECT_FAIL_ONCE=1, fail exactly once per process,
        then clear the env var so subsequent calls succeed.
        """
        if os.getenv("AMADEUS_INJECT_FAIL_ONCE", "").strip() not in ("1", "true", "TRUE", "yes", "YES"):
            return

        # Clear immediately so we only fail once even if caller retries.
        os.environ.pop("AMADEUS_INJECT_FAIL_ONCE", None)

        # Put status in message so agent retry logic can detect it.
        raise RuntimeError(f"status=503 injected transient Amadeus failure for {method}:{path}")

    # --------------------
    # Request helper (NO RETRIES)
    # --------------------
    def _request_once(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        self._maybe_inject_fail_once(method, path)

        url = f"{self.config.base_url}{path}"
        p = params or {}
        debug_summary = self._debug_request_summary(method, url, p) if self._debug_enabled() else None

        try:
            r = requests.request(
                method,
                url,
                headers=self._headers(),
                params=p,
                data=data,
                json=json,
                timeout=self.config.timeout_s,
            )
            r.raise_for_status()
            return r

        except (requests.Timeout, requests.ConnectionError) as e:
            msg = f"Amadeus {method} {url} failed (network/timeout)"
            if debug_summary:
                msg += f" debug={debug_summary}"
            raise RuntimeError(msg) from e

        except requests.HTTPError as e:
            resp = getattr(e, "response", None)
            status = None
            body_text = None
            try:
                status = resp.status_code if resp is not None else r.status_code  # type: ignore[name-defined]
            except Exception:
                status = None
            try:
                body_text = resp.text if resp is not None else None
            except Exception:
                body_text = None

            msg = f"Amadeus {method} {url} failed"
            if status is not None:
                msg += f" status={status}"
            if debug_summary:
                msg += f" debug={debug_summary}"
            if json is not None:
                try:
                    msg += f" json_keys={sorted(list(json.keys()))}"
                except Exception:
                    pass
            if body_text:
                msg += f" body={body_text[:1000]}"
            raise RuntimeError(msg) from e

        except Exception as e:
            msg = f"Amadeus {method} {url} failed (unexpected)"
            if debug_summary:
                msg += f" debug={debug_summary}"
            raise RuntimeError(msg) from e

    # --------------------
    # Public API
    # --------------------
    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        r = self._request_once("GET", path, params=params)
        return r.json()

    def post(
        self,
        path: str,
        *,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        r = self._request_once("POST", path, params=params, data=data, json=json)
        return r.json()


def make_client(config: Optional[AmadeusConfig] = None) -> AmadeusClient:
    """
    Canonical builder for adapters/tools.
    Tools should call make_client() rather than instantiating AmadeusClient directly.
    """
    cfg = config or load_amadeus_config_from_env()
    return AmadeusClient(config=cfg)
