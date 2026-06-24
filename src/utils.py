from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

def replace_tokens(value: Any, tokens: dict[str, Any]) -> Any:
	if isinstance(value, dict):
		return {k: replace_tokens(v, tokens) for k, v in value.items()}
	if isinstance(value, list):
		return [replace_tokens(item, tokens) for item in value]
	if isinstance(value, str):
		if value in tokens:
			return tokens[value]
		rendered = value
		for token, replacement in tokens.items():
			rendered = rendered.replace(token, str(replacement))
		return rendered
	return value

def load_json_template(template_name: str, tokens: dict[str, Any]) -> dict[str, Any]:

	template_path = Path(__file__).resolve().parent.parent / "input_data" / "jsons" / template_name
	if not template_path.exists():
		raise FileNotFoundError(f"Template not found: {template_path}")

	raw = template_path.read_text(encoding="utf-8").strip()
	if not raw:
		raise ValueError(f"Template file is empty: {template_path}")

	value = json.loads(raw)
	
	if isinstance(value, dict):
		return {k: replace_tokens(v, tokens) for k, v in value.items()}
	if isinstance(value, list):
		return [replace_tokens(item, tokens) for item in value]
	if isinstance(value, str):
		if value in tokens:
			return tokens[value]
		rendered = value
		for token, replacement in tokens.items():
			rendered = rendered.replace(token, str(replacement))
		return rendered
	return value

def search_rest_get(
	search_endpoint: str,
	search_admin_key: str,
	path: str,
	api_version: str = "2025-09-01",
) -> dict[str, Any]:
	url = f"{search_endpoint}/{path}?api-version={api_version}"
	response = requests.get(url, headers={"api-key": search_admin_key}, timeout=120)
	try:
		response.raise_for_status()
	except requests.HTTPError as ex:
		details = (response.text or "").strip()
		raise RuntimeError(
			f"Search GET failed for '{path}' with status {response.status_code}. Response: {details}"
		) from ex
	return response.json() if response.text else {}


def search_rest_put(
	search_endpoint: str,
	search_admin_key: str,
	path: str,
	payload: dict[str, Any],
	api_version: str = "2025-09-01",
) -> dict[str, Any]:
	url = f"{search_endpoint}/{path}?api-version={api_version}"
	headers = {"Content-Type": "application/json", "api-key": search_admin_key}
	response = requests.put(url, headers=headers, json=payload, timeout=120)
	try:
		response.raise_for_status()
	except requests.HTTPError as ex:
		details = (response.text or "").strip()
		raise RuntimeError(
			f"Search PUT failed for '{path}' with status {response.status_code}. Response: {details}"
		) from ex
	return response.json() if response.text else {}


def search_rest_post(
	search_endpoint: str,
	search_admin_key: str,
	path: str,
	payload: dict[str, Any] | None = None,
	api_version: str = "2025-09-01",
) -> dict[str, Any]:
	url = f"{search_endpoint}/{path}?api-version={api_version}"
	headers = {"Content-Type": "application/json", "api-key": search_admin_key}
	response = requests.post(url, headers=headers, json=payload if payload is not None else {}, timeout=120)
	try:
		response.raise_for_status()
	except requests.HTTPError as ex:
		details = (response.text or "").strip()
		raise RuntimeError(
			f"Search POST failed for '{path}' with status {response.status_code}. Response: {details}"
		) from ex
	return response.json() if response.text else {}

