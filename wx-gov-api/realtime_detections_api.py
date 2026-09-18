import argparse
import json
import os
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

IAM_URL = "https://iam.cloud.ibm.com/oidc/token"
SUPPORTED_REGIONS = {
    "us-south",
    "eu-de",
    "au-syd",
    "ca-tor",
    "jp-tok",
    "eu-gb",
}


@dataclass(frozen=True)
class RealtimeDetectionConfig:
    api_key: str
    service_instance_id: str
    region: str
    verify_ssl: bool

    @property
    def text_detection_url(self) -> str:
        return f"https://{self.region}.ml.cloud.ibm.com/ml/v1/text/detection"


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


def load_config() -> RealtimeDetectionConfig:
    load_dotenv()
    region = os.getenv("WATSONX_REGION") or os.getenv("IBM_CLOUD_REGION") or "us-south"
    if region not in SUPPORTED_REGIONS:
        supported_regions = ", ".join(sorted(SUPPORTED_REGIONS))
        raise RuntimeError(
            f"Unsupported IBM Cloud region '{region}'. Supported regions: {supported_regions}."
        )

    return RealtimeDetectionConfig(
        api_key=require_env("IBM_CLOUD_APIKEY"),
        service_instance_id=require_env(
            "WXG_SERVICE_INSTANCE_ID", "SERVICE_INSTANCE_ID"
        ),
        region=region,
        verify_ssl=parse_bool_env("VERIFY_SSL", True),
    )


def get_access_token(config: RealtimeDetectionConfig) -> str:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    auth = HTTPBasicAuth("bx", "bx")
    data = {
        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
        "apikey": config.api_key,
    }
    response = requests.post(
        IAM_URL,
        data=data,
        headers=headers,
        auth=auth,
        timeout=30,
        verify=config.verify_ssl,
    )
    if response.status_code == 200:
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError(
                "IBM Cloud IAM response did not include an access_token."
            )
        return token

    error_body = response.text
    try:
        error_body = response.json().get("error_description", response.text)
    except ValueError:
        pass
    raise RuntimeError(
        f"IBM Cloud authentication failed ({response.status_code}): {error_body}"
    )


def build_headers(token: str, service_instance_id: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "x-governance-instance-id": service_instance_id,
    }


def parse_detectors(
    detectors_json: str | None, hap_threshold: float | None
) -> dict[str, Any]:
    if detectors_json:
        try:
            detectors = json.loads(detectors_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid JSON passed to --detectors-json: {exc}"
            ) from exc
        if not isinstance(detectors, dict) or not detectors:
            raise RuntimeError(
                "--detectors-json must decode to a non-empty JSON object."
            )
        return detectors

    detectors: dict[str, Any] = {"pii": {}}
    if hap_threshold is not None:
        detectors["hap"] = {"threshold": hap_threshold}
    return detectors


def detect_text(
    config: RealtimeDetectionConfig,
    text: str,
    detectors: dict[str, Any],
) -> dict[str, Any]:
    token = get_access_token(config)
    headers = build_headers(token, config.service_instance_id)
    payload = {
        "detectors": detectors,
        "input": text,
    }
    response = requests.post(
        config.text_detection_url,
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
            f"Realtime detection failed ({response.status_code}): {error_body}"
        )
    return response.json()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Invoke the realtime watsonx governance text detection API."
    )
    parser.add_argument(
        "--text",
        default="I think lizards are disgusting and my email is abc@gmail.com",
        help="Text to submit to the realtime detection API.",
    )
    parser.add_argument(
        "--hap-threshold",
        type=float,
        default=0.5,
        help="Threshold for the HAP detector. Use --no-hap to omit HAP.",
    )
    parser.add_argument(
        "--no-hap",
        action="store_true",
        help="Disable the default HAP detector.",
    )
    parser.add_argument(
        "--detectors-json",
        help="Raw JSON object to use as the detectors payload. Overrides --hap-threshold and --no-hap.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    hap_threshold = None if args.no_hap else args.hap_threshold
    detectors = parse_detectors(args.detectors_json, hap_threshold=hap_threshold)
    result = detect_text(config, text=args.text, detectors=detectors)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
