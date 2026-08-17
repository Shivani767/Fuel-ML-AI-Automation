"""Automated ML fuel theft/filling monitor with domain-aware detection."""

import argparse
import json
import os
import time
from datetime import datetime

import joblib
import pandas as pd

from fetch_live_data import fetch_all_vehicles
from fuel_event_engine import detect_events, fix_datetime_str

MODEL_PATH = "models/fuel_event_model.pkl"
ALERTS_PATH = "fuel_data/ml_alerts.json"


def load_model():
    artifact = joblib.load(MODEL_PATH)
    if isinstance(artifact, dict):
        return artifact['model'], artifact.get('features', [])
    return artifact, []


def preprocess(data):
    df = pd.DataFrame(data)
    df['deviceDataInsertTime'] = df['deviceDataInsertTime'].apply(fix_datetime_str)
    df['deviceDataInsertTime'] = pd.to_datetime(df['deviceDataInsertTime'], errors='coerce')
    for col in ['fuelInLiter', 'iO_1', 'iO_9', 'iO_66', 'iO_67', 'speed', 'latitude', 'longitude']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna(subset=['deviceDataInsertTime', 'fuelInLiter', 'iO_1', 'iO_9']).sort_values(
        ['deviceInfoId', 'deviceDataInsertTime']
    )


def predict_all(df, model):
    alerts = []
    for device_id in df['deviceInfoId'].unique():
        device_df = df[df['deviceInfoId'] == device_id].copy()
        alerts.extend(detect_events(device_df, model=model))
    return alerts


def save_alerts(alerts, path=ALERTS_PATH, merge=False):
    if merge and os.path.exists(path):
        with open(path, 'r') as f:
            existing = json.load(f)
        seen = {(a['deviceName'], a['eventTime'], a['eventType']) for a in existing}
        alerts = existing + [a for a in alerts if (a['deviceName'], a['eventTime'], a['eventType']) not in seen]

    alerts.sort(key=lambda a: a['eventTime'], reverse=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2, default=str)
    print(f"Saved {len(alerts)} ML alerts to {path}")


def notify(alerts):
    thefts = [a for a in alerts if a['eventType'] == 'Fuel Theft']
    fillings = [a for a in alerts if a['eventType'] == 'Fuel Filling']
    print(f"\n{'='*60}")
    print(f"ML ALERTS: {len(thefts)} THEFT | {len(fillings)} FILLING")
    print('='*60)
    for a in thefts[:15]:
        print(f"  [THEFT]   {a['deviceName']} at {a['eventTime']}: {a['fuelChange']}L, EDC {a['edcChange']} ({a['location']})")
    for a in fillings[:15]:
        print(f"  [FILLING] {a['deviceName']} at {a['eventTime']}: +{a['fuelChange']}L, EDC +{a['edcChange']} ({a['location']})")


def resolve_data_path(explicit=None):
    for p in [explicit, "fuel_data/june_2026_data.json", "fuel_data/live_fuel_data.json"]:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError("No data file found.")


def run_once(model, data_path=None):
    data_path = resolve_data_path(data_path)
    print(f"Loading data from {data_path}...")
    with open(data_path, 'r') as f:
        data = json.load(f)
    df = preprocess(data)
    print(f"Running ML detection on {len(df)} points...")
    alerts = predict_all(df, model)
    notify(alerts)
    save_alerts(alerts)
    return alerts


def watch(model, interval_seconds=300, lookback_days=1):
    print(f"Live ML monitor (every {interval_seconds}s)...")
    while True:
        print(f"\n--- Poll at {datetime.now().isoformat()} ---")
        data = fetch_all_vehicles(lookback_days=lookback_days)
        if data:
            df = preprocess(data)
            alerts = predict_all(df, model)
            if alerts:
                notify(alerts)
                save_alerts(alerts, merge=True)
        time.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(description="ML fuel theft/filling monitor")
    parser.add_argument("--data", default=None)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--lookback-days", type=int, default=1)
    args = parser.parse_args()

    model, _ = load_model()
    print(f"Loaded model from {MODEL_PATH}")

    if args.watch:
        watch(model, args.interval, args.lookback_days)
    else:
        run_once(model, args.data)


if __name__ == "__main__":
    main()
