"""Shared FuelArk API utilities for live data fetching."""

import os
import time
import requests
from datetime import datetime, timedelta

from env import load_env

# Credentials live in .env (gitignored), not in source.
load_env()

BASE_URL = os.environ.get("FUELARK_BASE_URL", "https://web1.fuelark.com")
API_KEY_URL = os.environ.get("FUELARK_API_URL", "https://api1.fuelark.com/api/FuelConsumption/DeviceFuelConsumption")
USERNAME = os.environ.get("FUELARK_USERNAME", "")
PASSWORD = os.environ.get("FUELARK_PASSWORD", "")
API_KEY = os.environ.get("FUELARK_API_KEY", "")

LOGIN_PATHS = [
    "/api/Account/Login",
    "/api/account/login",
    "/api/Auth/Login",
    "/api/auth/login",
]


def login_and_get_token(username=None, password=None, base_url=None):
    username = username or USERNAME
    password = password or PASSWORD
    base_url = base_url or BASE_URL
    for path in LOGIN_PATHS:
        login_url = f"{base_url}{path}"
        try:
            response = requests.post(
                login_url,
                headers={"Content-Type": "application/json"},
                json={"Username": username, "Password": password},
                timeout=30,
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            data = response.json()
            if data.get("StatusCode") == 200:
                return data.get("ResponseData", {}).get("Token")
        except requests.exceptions.RequestException:
            continue
    print("Bearer login failed on all known endpoints, falling back to API key auth.")
    return None


def fetch_live_data_bearer(imei, token, start_datetime, end_datetime, base_url=None):
    base_url = base_url or BASE_URL
    api_url = f"{base_url}/api/FuelConsumption/DeviceFuelConsumption"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    params = {
        "FromDatetime": start_datetime.isoformat(),
        "ToDatetime": end_datetime.isoformat(),
        "IMEI": imei,
    }
    try:
        response = requests.get(api_url, headers=headers, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()
        if data.get("StatusCode") == 200 and data.get("ResponseData"):
            return data["ResponseData"].get("DeviceFuelInfo", [])
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error fetching live data for {imei}: {e}")
        return []


def fetch_live_data_api_key(imei, date_str, retries=3):
    from_datetime = f"{date_str} 00:00:00"
    to_datetime = f"{date_str} 23:59:59"
    headers = {"x-api-key": API_KEY, "Content-Type": "application/json"}
    params = {"fromdate": from_datetime, "todate": to_datetime, "IMEI": imei}
    for attempt in range(retries):
        try:
            response = requests.get(API_KEY_URL, headers=headers, params=params, timeout=90)
            response.raise_for_status()
            response_json = response.json()
            if "statusCode" in response_json and response_json["statusCode"] == 1:
                return response_json["responseData"].get("deviceFuelInfo", [])
            if "StatusCode" in response_json and response_json["StatusCode"] == 200:
                return response_json["ResponseData"].get("DeviceFuelInfo", [])
            return []
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"Error fetching data for {imei} on {date_str}: {e}")
    return []


def fetch_live_window(imei, start_datetime, end_datetime, retries=3):
    """Fetch an arbitrary time window for one IMEI, in a single call.

    `fetch_live_data_api_key` always asks for 00:00:00-23:59:59, so refreshing a
    live dashboard re-downloaded the whole day from every device on every cycle --
    at 16:00 that is ~16 hours of points per device, repeatedly. The API honours
    arbitrary fromdate/todate, so a delta refresh only needs the last few minutes.
    """
    headers = {"x-api-key": API_KEY, "Content-Type": "application/json"}
    params = {
        "fromdate": start_datetime.strftime("%Y-%m-%d %H:%M:%S"),
        "todate": end_datetime.strftime("%Y-%m-%d %H:%M:%S"),
        "IMEI": imei,
    }
    for attempt in range(retries):
        try:
            response = requests.get(API_KEY_URL, headers=headers, params=params, timeout=90)
            response.raise_for_status()
            payload = response.json()
            if payload.get("statusCode") == 1:
                return payload.get("responseData", {}).get("deviceFuelInfo", []) or []
            if payload.get("StatusCode") == 200:
                return payload.get("ResponseData", {}).get("DeviceFuelInfo", []) or []
            return []
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"Error fetching window for {imei}: {e}")
    return []


def fetch_imei_range(imei, lookback_days=7, token=None):
    end_datetime = datetime.now()
    start_datetime = end_datetime - timedelta(days=lookback_days)
    return fetch_imei_dates(imei, start_datetime, end_datetime, token=token)


def fetch_imei_dates(imei, start_datetime, end_datetime, token=None):
    if token:
        return fetch_live_data_bearer(imei, token, start_datetime, end_datetime)
    all_points = []
    current = start_datetime.date()
    last = end_datetime.date()
    while current <= last:
        points = fetch_live_data_api_key(imei, current.strftime("%Y-%m-%d"))
        all_points.extend(points)
        current += timedelta(days=1)
    return all_points


# The API returns 55 fields per record. This 14-field subset was all that used to be
# persisted, which silently discarded several that matter for detection accuracy:
#   iO_16       odometer in metres (verified strictly non-decreasing, ~119.8 km/day)
#   satellites  GPS fix quality, observed swinging 3-22 within a single day
#   deviceType  device class, useful for filling unknown vehicle types
# Historical dumps cannot be re-fetched -- the API returns 0 records for dates more
# than roughly a week old -- so anything not captured today is lost permanently.
# Full capture is therefore the default; trimming is opt-in.
CORE_FIELDS = [
    "imei", "deviceInfoId", "deviceName", "deviceDataInsertTime",
    "fuelInLiter", "iO_1", "iO_2", "iO_6", "iO_9", "iO_66", "iO_67",
    "speed", "latitude", "longitude",
]

# Fields beyond CORE_FIELDS that detection actively uses. Kept as a named set so a
# caller that must trim still retains the ones that affect accuracy.
EXTRA_FIELDS = ["iO_16", "satellites", "altitude", "angle", "deviceType", "deviceTypeName"]

KEEP_FIELDS = CORE_FIELDS + EXTRA_FIELDS  # back-compat for existing importers


def trim_point(point, fields=None):
    """Return a record for persistence.

    Default is the untouched payload. Pass `fields` (e.g. `CORE_FIELDS`) only when
    file size genuinely matters -- dropped fields are unrecoverable later.
    """
    if fields is None:
        return dict(point)
    return {k: point.get(k) for k in fields}
