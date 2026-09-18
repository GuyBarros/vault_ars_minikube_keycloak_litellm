import argparse
import json
import logging
from numbers import Real
import os

from dotenv import load_dotenv
import requests
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator

from ibm_watsonx_gov.evaluators import MetricsEvaluator
from ibm_watsonx_gov.metrics import (
    HAPMetric,
    JailbreakMetric,
    HarmMetric,
    UnethicalBehaviorMetric,
    PromptSafetyRiskMetric,
)
from structured_logging import get_logger, log_event

RESOURCE_CONTROLLER_URL = (
    "https://resource-controller.cloud.ibm.com/v2/resource_instances"
)
OPENSCALE_RESOURCE_ID = "2ad019f3-0fd6-4c25-966d-f3952481a870"
logger = get_logger(__name__)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set.")
    return value


def resolve_service_instance_id(api_key: str, region: str) -> str:
    authenticator = IAMAuthenticator(apikey=api_key)
    token = authenticator.token_manager.get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    matching_resources: list[dict] = []
    next_url = RESOURCE_CONTROLLER_URL
    while next_url:
        response = requests.get(next_url, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()

        for resource in payload.get("resources", []):
            if (
                resource.get("resource_id") == OPENSCALE_RESOURCE_ID
                and resource.get("region_id") == region
            ):
                matching_resources.append(resource)

        next_path = payload.get("next_url")
        next_url = (
            f"https://resource-controller.cloud.ibm.com{next_path}"
            if next_path
            else None
        )

    if not matching_resources:
        raise RuntimeError(
            f"Could not find a watsonx.governance service instance in region {region}. "
            "Set WXG_SERVICE_INSTANCE_ID explicitly if your instance is in a different region."
        )

    if len(matching_resources) > 1:
        raise RuntimeError(
            f"Found multiple watsonx.governance service instances in region {region}. "
            "Set WXG_SERVICE_INSTANCE_ID explicitly to choose the correct instance."
        )

    return matching_resources[0]["guid"]


def configure_environment() -> None:
    load_dotenv()

    api_key = require_env("IBM_CLOUD_APIKEY")
    region = os.getenv("WATSONX_REGION") or os.getenv("IBM_CLOUD_REGION") or "us-south"
    os.environ["WATSONX_APIKEY"] = api_key
    os.environ["WATSONX_REGION"] = region

    service_instance_id = os.getenv("WXG_SERVICE_INSTANCE_ID")
    if not service_instance_id:
        service_instance_id = resolve_service_instance_id(
            api_key=api_key, region=region
        )
    os.environ["WXG_SERVICE_INSTANCE_ID"] = service_instance_id


def evaluate_text_metrics(text: str) -> dict[str, float]:
    configure_environment()
    log_event(
        logger,
        logging.DEBUG,
        "guardrails.metrics.started",
        "Starting realtime metrics evaluation",
    )

    evaluator = MetricsEvaluator()
    result = evaluator.evaluate(
        data={"input_text": text},
        metrics=[
            HarmMetric(),
            HAPMetric(),
            PromptSafetyRiskMetric(method="granite_guardian"),
            JailbreakMetric(),
            UnethicalBehaviorMetric(),
        ],
    )
    logger.debug(
        "Realtime metrics raw result",
        extra={"event": "guardrails.metrics.raw_result", "raw_result": str(result)},
    )

    result_frame = result.to_df()
    if result_frame.empty:
        raise RuntimeError("Realtime detection did not return any metric results.")

    metric_scores: dict[str, float] = {}
    for column_name, value in result_frame.iloc[0].items():
        if isinstance(value, bool) or not isinstance(value, Real):
            continue
        metric_scores[column_name.split(".", 1)[0]] = float(value)

    if not metric_scores:
        raise RuntimeError(
            "Realtime detection did not return any numeric metric scores."
        )

    log_event(
        logger,
        logging.INFO,
        "guardrails.metrics.completed",
        f"Policy evaluated for user input: {text}, response: {json.dumps(metric_scores, ensure_ascii=False)}",
        response=metric_scores,
    )
    return metric_scores


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate input text for HAP, PII, prompt safety risk, and jailbreak risk."
    )
    parser.add_argument(
        "--text",
        nargs="+",
        help="Input text to evaluate.",
        required=True,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = " ".join(args.text)
    # print(evaluate_text_metrics(text))
    print(json.dumps(evaluate_text_metrics(text), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
