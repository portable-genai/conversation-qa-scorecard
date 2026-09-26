"""Managed GuardrailPort: Model Armor (rule R1).

Every text this service hands to the narrator or the signal classifier is screened via
``sanitizeUserPrompt`` / ``sanitizeModelResponse`` against a regional Model Armor template
(``config/settings.yaml`` ``model_armor.template_id``, on the regional host
``model_armor.host``, pinned for residency, P-05). Reuses the shape proved at cio-advisory
parity (``cio_advisory.adapters.gcp.model_armor_guardrail.ModelArmorGuardrailAdapter``).

The ``google.cloud.modelarmor`` import is lazy so the ``local``/``onprem`` profiles import this
module with no GCP SDK installed.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.kernel import Direction, GuardrailCategory, GuardrailFinding, GuardrailVerdict


class ModelArmorGuardrailAdapter:
    """Screen prompts/responses through a regional Model Armor template."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._armor = settings.model_armor
        self._client: Any | None = None

    def _template(self) -> str:
        return (
            f"projects/{self._settings.project_id}/locations/{self._settings.region}"
            f"/templates/{self._armor.template_id}"
        )

    def _get_client(self) -> Any:  # pragma: no cover - needs live GCP
        from google.cloud import modelarmor_v1 as ma  # lazy

        if self._client is None:
            self._client = ma.ModelArmorClient(client_options={"api_endpoint": self._armor.host})
        return self._client

    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:  # pragma: no cover
        """Screen inbound prompt or outbound response; may sanitise in place."""
        from google.cloud import modelarmor_v1 as ma  # lazy

        client = self._get_client()
        template = self._template()
        if direction is Direction.INPUT:
            response = client.sanitize_user_prompt(
                request=ma.SanitizeUserPromptRequest(
                    name=template,
                    user_prompt_data=ma.DataItem(text=text),
                )
            )
        else:
            response = client.sanitize_model_response(
                request=ma.SanitizeModelResponseRequest(
                    name=template,
                    model_response_data=ma.DataItem(text=text),
                )
            )
        return self._map_result(response, direction, text)

    @staticmethod
    def _map_result(response: Any, direction: Direction, original: str) -> GuardrailVerdict:
        result = getattr(response, "sanitization_result", None)
        match_state = str(getattr(result, "filter_match_state", "")).upper()
        blocked = "MATCH_FOUND" in match_state and "NO_MATCH" not in match_state
        findings = (
            (
                GuardrailFinding(
                    category=GuardrailCategory.OTHER,
                    confidence="high",
                    detail="Model Armor filter match",
                ),
            )
            if blocked
            else ()
        )
        return GuardrailVerdict(
            allowed=not blocked,
            direction=direction,
            findings=findings,
            sanitized_text=None if blocked else original,
            reason="blocked by Model Armor" if blocked else "ok",
        )
