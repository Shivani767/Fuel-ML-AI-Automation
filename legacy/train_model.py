import argparse
import json
import os

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

from fuel_event_engine import FEATURE_COLS, fix_datetime_str, label_device


def resolve_data_path(explicit_path=None):
    candidates = [
        explicit_path,
        "fuel_data/june_2026_data.json",
        "fuel_data/live_fuel_data.json",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    raise FileNotFoundError("No training data found. Run: python fetch_live_data.py")


def load_and_preprocess_data(data_path=None):
    all_data_path = resolve_data_path(data_path)
    print(f"Loading training data from {all_data_path}")
    with open(all_data_path, 'r') as f:
        data = json.load(f)

    df = pd.DataFrame(data)
    df['deviceDataInsertTime'] = df['deviceDataInsertTime'].apply(fix_datetime_str)
    df['deviceDataInsertTime'] = pd.to_datetime(df['deviceDataInsertTime'], errors='coerce')
    for col in ['fuelInLiter', 'iO_1', 'iO_9', 'iO_66', 'iO_67', 'speed', 'latitude', 'longitude']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.dropna(subset=['deviceDataInsertTime', 'fuelInLiter', 'iO_1', 'iO_9'])
    df = df.sort_values(['deviceInfoId', 'deviceDataInsertTime'])

    verified_events = []
    if os.path.exists("fuel_data/verified_theft_events.json"):
        with open("fuel_data/verified_theft_events.json", 'r') as f:
            verified_events = json.load(f)

    fuel_events = []
    if os.path.exists("fuel_data/fuel_events.json"):
        with open("fuel_data/fuel_events.json", 'r') as f:
            fuel_events = json.load(f)

    features_list = []
    device_ids = df['deviceInfoId'].unique()
    print(f"Labeling {len(device_ids)} devices with filling/theft/slope rules...")

    for idx, device_id in enumerate(device_ids, 1):
        device_df = df[df['deviceInfoId'] == device_id].copy()
        labeled = label_device(device_df, verified_events, fuel_events)
        features_list.append(labeled)
        if idx % 20 == 0:
            print(f"  Processed {idx}/{len(device_ids)} devices")

    full_df = pd.concat(features_list, ignore_index=True)
    print(f"Full DataFrame shape after preprocessing: {full_df.shape}")
    print(f"Slope cases filtered: {full_df['is_slope'].sum()}")
    print(f"Class distribution:\n{full_df['target'].value_counts()}")

    available = [c for c in FEATURE_COLS if c in full_df.columns]
    X = full_df[available].copy()
    y = full_df['target'].copy()
    return X, y, full_df


def train_model(X, y):
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y
    )
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=18,
        min_samples_split=5,
        random_state=42,
        class_weight='balanced',
    )
    model.fit(X_train, y_train)

    print("Model Evaluation:")
    print("\nConfusion Matrix:")
    print(confusion_matrix(y_test, model.predict(X_test)))
    print("\nClassification Report:")
    print(classification_report(y_test, model.predict(X_test), target_names=['Normal', 'Theft', 'Filling']))
    return model


def save_model(model, feature_cols):
    os.makedirs("models", exist_ok=True)
    joblib.dump({'model': model, 'features': feature_cols}, "models/fuel_event_model.pkl")
    print("\nModel saved to models/fuel_event_model.pkl")


def main():
    parser = argparse.ArgumentParser(description="Train fuel theft/filling ML model")
    parser.add_argument("--data", default=None, help="Path to training data JSON")
    args = parser.parse_args()

    X, y, _ = load_and_preprocess_data(args.data)
    print(f"\nData shape: {X.shape}")
    print(f"Features: {list(X.columns)}")

    print("\nTraining model...")
    model = train_model(X, y)
    save_model(model, list(X.columns))

    print("\nFeature Importances:")
    print(pd.Series(model.feature_importances_, index=X.columns).sort_values(ascending=False))


if __name__ == "__main__":
    main()
