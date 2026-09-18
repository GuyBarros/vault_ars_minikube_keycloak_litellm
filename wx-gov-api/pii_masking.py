import argparse
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv
from structured_logging import get_logger, log_event

IAM_URL = "https://iam.cloud.ibm.com/identity/token"
REGION_BASE_URLS = {
    "us-south": "https://api.aiopenscale.cloud.ibm.com",
    "eu-de": "https://eu-de.api.aiopenscale.cloud.ibm.com",
    "au-syd": "https://au-syd.api.aiopenscale.cloud.ibm.com",
    "ca-tor": "https://ca-tor.api.aiopenscale.cloud.ibm.com",
    "jp-tok": "https://jp-tok.api.aiopenscale.cloud.ibm.com",
    "eu-gb": "https://eu-gb.api.aiopenscale.cloud.ibm.com",
}
logger = get_logger(__name__)


@dataclass(frozen=True)
class GuardrailConfig:
    api_key: str
    inventory_id: str
    governance_instance_id: str
    policy_id: str
    region: str
    base_url: str
    verify_ssl: bool
    system_prompt: str | None


def require_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    joined_names = ", ".join(names)
    raise RuntimeError(
        f"Required environment variable not set. Expected one of: {joined_names}."
    )


def parse_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def get_base_url(region: str) -> str:
    try:
        return REGION_BASE_URLS[region]
    except KeyError as exc:
        supported_regions = ", ".join(sorted(REGION_BASE_URLS))
        raise RuntimeError(
            f"Unsupported IBM Cloud region '{region}'. Supported regions: {supported_regions}."
        ) from exc


def load_config() -> GuardrailConfig:
    load_dotenv()

    region = os.getenv("WATSONX_REGION") or os.getenv("IBM_CLOUD_REGION") or "us-south"
    base_url = os.getenv("GUARDRAIL_BASE_URL") or get_base_url(region)

    return GuardrailConfig(
        api_key=require_env("IBM_CLOUD_APIKEY"),
        inventory_id=require_env("INVENTORY_ID"),
        governance_instance_id=require_env(
            "WXG_SERVICE_INSTANCE_ID", "SERVICE_INSTANCE_ID"
        ),
        policy_id=require_env("CUSTOM_GUARDRAIL_POLICY_ID", "POLICY_ID"),
        region=region,
        base_url=base_url.rstrip("/"),
        verify_ssl=parse_bool_env("VERIFY_SSL", True),
        system_prompt=os.getenv("GUARDRAIL_SYSTEM_PROMPT"),
    )


def get_cloud_access_token(api_key: str, verify_ssl: bool) -> str:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = {
        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
        "apikey": api_key,
    }

    response = requests.post(
        IAM_URL, data=data, headers=headers, timeout=30, verify=verify_ssl
    )
    if response.status_code == 200:
        access_token = response.json().get("access_token")
        if not access_token:
            raise RuntimeError(
                "IBM Cloud IAM response did not include an access_token."
            )
        return access_token

    error_body = response.text
    try:
        error_body = response.json().get("error_description", response.text)
    except ValueError:
        pass
    raise RuntimeError(
        f"IBM Cloud authentication failed ({response.status_code}): {error_body}"
    )


def build_headers(access_token: str, governance_instance_id: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-governance-instance-id": governance_instance_id,
        "Authorization": f"Bearer {access_token}",
    }


def build_detectors_properties(system_prompt: str | None) -> dict[str, dict[str, str]]:
    detectors_properties: dict[str, dict[str, str]] = {
        "pii": {},
        # "prompt_safety_risk": {},
        # "hap": {},
        # "harm": {},
        # "jailbreak": {},
        # "social_bias": {},
        # "profanity": {},
        # "sexual_content": {},
        # "unethical_behavior": {},
        # "violence": {},
    }
    prompt_based_detectors = {"topic_relevance", "prompt_safety_risk"}
    for detector_name in prompt_based_detectors.intersection(detectors_properties):
        if not system_prompt:
            raise RuntimeError(
                f"Detector '{detector_name}' requires GUARDRAIL_SYSTEM_PROMPT to be set."
            )
        detectors_properties[detector_name] = {"system_prompt": system_prompt}

    if not detectors_properties:
        raise RuntimeError("At least one detector must be configured for enforcement.")

    return detectors_properties


def list_detectors(config: GuardrailConfig, detector_set: str) -> list[dict[str, Any]]:
    access_token = get_cloud_access_token(config.api_key, config.verify_ssl)
    headers = build_headers(access_token, config.governance_instance_id)
    response = requests.get(
        f"{config.base_url}/guardrails-manager/v1/detectors",
        headers=headers,
        params={"inventory_id": config.inventory_id},
        timeout=30,
        verify=config.verify_ssl,
    )
    response.raise_for_status()

    payload = response.json()
    key = "custom_detectors" if detector_set == "custom" else "detectors"
    return payload.get(key, [])


def list_policies(config: GuardrailConfig, policy_type: str) -> dict[str, Any]:
    access_token = get_cloud_access_token(config.api_key, config.verify_ssl)
    headers = build_headers(access_token, config.governance_instance_id)
    response = requests.get(
        f"{config.base_url}/guardrails-manager/v1/policies",
        headers=headers,
        params={
            "inventory_id": config.inventory_id,
            "policytype": policy_type,
        },
        timeout=30,
        verify=config.verify_ssl,
    )
    response.raise_for_status()
    return response.json()


def enforce_policy(
    config: GuardrailConfig, text: str, direction: str
) -> dict[str, Any]:
    access_token = get_cloud_access_token(config.api_key, config.verify_ssl)
    headers = build_headers(access_token, config.governance_instance_id)
    payload = {
        "text": text,
        "direction": direction,
        "detectors_properties": build_detectors_properties(config.system_prompt),
    }
    response = requests.post(
        f"{config.base_url}/guardrails-manager/v1/enforce/{config.policy_id}",
        params={"inventory_id": config.inventory_id},
        headers=headers,
        json=payload,
        timeout=60,
        verify=config.verify_ssl,
    )
    if not response.ok:
        error_body = response.text
        try:
            error_body = json.dumps(response.json(), indent=2, ensure_ascii=False)
        except ValueError:
            pass
        raise RuntimeError(
            f"Guardrail enforcement failed ({response.status_code}): {error_body}"
        )
    return response.json()


def get_nested_value(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current_value: Any = payload
    for key in path:
        if not isinstance(current_value, dict) or key not in current_value:
            return None
        current_value = current_value[key]
    return current_value


def extract_masked_text(response: dict[str, Any], original_text: str) -> str:
    candidate = get_nested_value(response, ("entity", "text"))
    if isinstance(candidate, str):
        return candidate

    raise RuntimeError(
        "Guardrail enforcement response did not include masked text in entity.text."
    )


def mask_pii_text(text: str, direction: str = "input") -> str:
    config = load_config()
    log_event(
        logger,
        logging.DEBUG,
        "guardrails.masking.started",
        "Starting PII masking enforcement",
        direction=direction,
        policy_id=config.policy_id,
    )
    response = enforce_policy(config, text=text, direction=direction)
    masked_text = extract_masked_text(response, original_text=text)
    if masked_text != text:
        log_event(
            logger,
            logging.INFO,
            "guardrails.masking.completed",
            "Completed watsonx.governance PII masking enforcement",
            direction=direction,
            policy_id=config.policy_id,
        )
    return masked_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List detectors or policies, or enforce a custom guardrail policy."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list_parser = subparsers.add_parser(
    #     "list-detectors", help="List available guardrail detectors."
    # )
    # list_parser.add_argument(
    #     "--detector-set",
    #     choices=("custom", "builtin"),
    #     default="custom",
    #     help="Choose whether to print custom or built-in detectors.",
    # )

    # policies_parser = subparsers.add_parser(
    #     "list-policies", help="List guardrail policies."
    # )
    # policies_parser.add_argument(
    #     "--policy-type",
    #     choices=("publish", "draft", "false"),
    #     default="publish",
    #     help="Filter policies by type: published, draft, or all.",
    # )

    enforce_parser = subparsers.add_parser(
        "enforce", help="Enforce a guardrail policy against text."
    )
    enforce_parser.add_argument(
        "--text", required=True, help="Text to validate against the policy."
    )
    enforce_parser.add_argument(
        "--direction",
        choices=("input", "output"),
        default="input",
        help="Whether the evaluated text is an input or output.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()

    # if args.command == "list-detectors":
    #     detectors = list_detectors(config, detector_set=args.detector_set)
    #     print(json.dumps(detectors, indent=2, ensure_ascii=False))
    #     return

    # if args.command == "list-policies":
    #     policies = list_policies(config, policy_type=args.policy_type)
    #     policy_list = policies.get("policies", [])
    #     print(f"Found {len(policy_list)} policies")
    #     print(json.dumps(policies, indent=2, ensure_ascii=False))
    #     return

    result = enforce_policy(config, text=args.text, direction=args.direction)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
