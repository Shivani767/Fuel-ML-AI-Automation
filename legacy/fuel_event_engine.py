"""Domain logic for fuel filling vs theft detection.

Filling: EDC rises, ignition mostly OFF, power disconnected, fuel up in 10-15 min.
Theft:   EDC drops sharply in 10-15 min, little/no movement (ignition on/off).
Slope:   Temporary EDC/fuel swing that recovers within 30-40 min — NOT an event.
"""

import numpy as np
import pandas as pd

WINDOW_SHORT_MIN = 15
WINDOW_SLOPE_MIN = 40
FUEL_FILL_MIN = 3.0
FUEL_THEFT_MIN = 3.0
EDC_FILL_MIN = 1
EDC_THEFT_MIN = 1
SLOPE_RECOVERY_FUEL = 2.0
SLOPE_RECOVERY_EDC = 2
IGNITION_OFF_RATIO_FILL = 0.6
MAX_SPEED_THEFT = 5.0
POWER_VOLTAGE_MV = 11000

FEATURE_COLS = [
    'fuelInLiter', 'iO_9', 'iO_1', 'iO_66', 'iO_67',
    'fuel_change', 'edc_change', 'time_diff',
    'fuel_net_15m', 'edc_net_15m',
    'ignition_off_ratio_15m', 'power_off_ratio_15m',
    'speed_max_15m', 'movement_15m',
    'fuel_recovery_40m', 'edc_recovery_40m', 'is_slope',
]


def fix_datetime_str(time_str):
    if '.' in str(time_str):
        parts = str(time_str).split('.')
        if len(parts[1]) < 3:
            parts[1] = parts[1].ljust(3, '0')
        return '.'.join(parts)
    return time_str


def _rolling_net(series, times, window_min):
    """Net change from window start to current point (fast, no pandas apply)."""
    values = series.values.astype(float)
    times = pd.to_datetime(times).values
    result = np.full(len(values), np.nan)
    delta = np.timedelta64(window_min, 'm')
    for i in range(len(values)):
        t_start = times[i] - delta
        j = np.searchsorted(times, t_start, side='left')
        result[i] = values[i] - values[j]
    return result


def _rolling_stat(times, values, window_min, stat='mean'):
    """Rolling stat over time window using searchsorted."""
    times = pd.to_datetime(times).values
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan)
    delta = np.timedelta64(window_min, 'm')
    for i in range(len(values)):
        t_start = times[i] - delta
        j = np.searchsorted(times, t_start, side='left')
        window = values[j:i + 1]
        if len(window) == 0:
            continue
        if stat == 'mean':
            result[i] = window.mean()
        elif stat == 'max':
            result[i] = window.max()
        elif stat == 'off_ratio':
            result[i] = (window == 0).mean()
        elif stat == 'range':
            result[i] = window.max() - window.min()
    return result


def add_rolling_features(device_df):
    """Add 15-min event window and 40-min slope-recovery features."""
    df = device_df.sort_values('deviceDataInsertTime').copy()
    df['deviceDataInsertTime'] = pd.to_datetime(df['deviceDataInsertTime'], errors='coerce')

    for col in ['fuelInLiter', 'iO_1', 'iO_9', 'iO_66', 'iO_67', 'speed', 'latitude', 'longitude']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df['prev_fuel'] = df['fuelInLiter'].shift(1)
    df['prev_edc'] = df['iO_9'].shift(1)
    df['prev_ignition'] = df['iO_1'].shift(1)
    df['time_diff'] = df['deviceDataInsertTime'].diff().dt.total_seconds()
    df['fuel_change'] = df['fuelInLiter'] - df['prev_fuel']
    df['edc_change'] = df['iO_9'] - df['prev_edc']

    if 'speed' not in df.columns:
        df['speed'] = 0.0
    if 'latitude' not in df.columns:
        df['latitude'] = 0.0
    if 'longitude' not in df.columns:
        df['longitude'] = 0.0

    df['power_off'] = (df['iO_66'].fillna(0) < POWER_VOLTAGE_MV).astype(float)

    times = df['deviceDataInsertTime']
    df['fuel_net_15m'] = _rolling_net(df['fuelInLiter'], times, WINDOW_SHORT_MIN)
    df['edc_net_15m'] = _rolling_net(df['iO_9'], times, WINDOW_SHORT_MIN)
    df['ignition_off_ratio_15m'] = _rolling_stat(times, df['iO_1'].values, WINDOW_SHORT_MIN, 'off_ratio')
    df['power_off_ratio_15m'] = _rolling_stat(times, df['power_off'].values, WINDOW_SHORT_MIN, 'mean')
    df['speed_max_15m'] = _rolling_stat(times, df['speed'].fillna(0).values, WINDOW_SHORT_MIN, 'max')
    lat_range = _rolling_stat(times, df['latitude'].fillna(0).values, WINDOW_SHORT_MIN, 'range')
    lon_range = _rolling_stat(times, df['longitude'].fillna(0).values, WINDOW_SHORT_MIN, 'range')
    df['movement_15m'] = lat_range + lon_range
    df['fuel_recovery_40m'] = np.abs(_rolling_net(df['fuelInLiter'], times, WINDOW_SLOPE_MIN))
    df['edc_recovery_40m'] = np.abs(_rolling_net(df['iO_9'], times, WINDOW_SLOPE_MIN))
    df['is_slope'] = df.apply(_is_slope_row, axis=1).astype(int)
    return df


def _is_slope_row(row):
    """Temporary fuel/EDC swing that returns to baseline within 40 min."""
    fuel_net = row.get('fuel_net_15m', 0) or 0
    edc_net = row.get('edc_net_15m', 0) or 0
    fuel_rec = row.get('fuel_recovery_40m', 999) or 999
    edc_rec = row.get('edc_recovery_40m', 999) or 999

    significant = abs(fuel_net) >= FUEL_THEFT_MIN or abs(edc_net) >= EDC_THEFT_MIN
    recovered = fuel_rec <= SLOPE_RECOVERY_FUEL and edc_rec <= SLOPE_RECOVERY_EDC
    return bool(significant and recovered)


def classify_row(row):
    """Return 0=normal, 1=theft, 2=filling using domain rules."""
    if row.get('is_slope', 0):
        return 0

    fuel_net = row.get('fuel_net_15m', 0) or 0
    edc_net = row.get('edc_net_15m', 0) or 0
    ign_off = row.get('ignition_off_ratio_15m', 0) or 0
    speed_max = row.get('speed_max_15m', 0) or 0
    movement = row.get('movement_15m', 0) or 0

    if abs(fuel_net) < 1 and abs(edc_net) < 3:
        return 0

    # Filling: EDC rises, fuel rises over 15 min, ignition mostly off
    if (fuel_net >= FUEL_FILL_MIN and edc_net >= EDC_FILL_MIN
            and ign_off >= IGNITION_OFF_RATIO_FILL):
        return 2

    # Theft: EDC drops sharply in 15 min, little movement
    if (edc_net <= -EDC_THEFT_MIN and fuel_net <= -1
            and speed_max <= MAX_SPEED_THEFT and movement < 0.01):
        return 1

    if (edc_net <= -EDC_THEFT_MIN * 3 and speed_max <= MAX_SPEED_THEFT):
        return 1

    return 0


def label_device(device_df, verified_thefts=None, fuel_events=None):
    """Label each row for ML training."""
    df = add_rolling_features(device_df)
    df['target'] = df.apply(classify_row, axis=1)

    device_names = df['deviceName'].dropna().unique() if 'deviceName' in df.columns else []

    for theft in (verified_thefts or []):
        if theft.get('deviceName') not in device_names:
            continue
        try:
            event_time = pd.to_datetime(theft['eventTime'])
            idx = (df['deviceDataInsertTime'] - event_time).abs().idxmin()
            if not df.loc[idx, 'is_slope']:
                df.loc[idx, 'target'] = 1
        except Exception:
            pass

    for filling in (fuel_events or []):
        if filling.get('eventType') != 'Fuel Filling':
            continue
        if filling.get('deviceName') not in device_names:
            continue
        try:
            event_time = pd.to_datetime(filling['eventTime'])
            idx = (df['deviceDataInsertTime'] - event_time).abs().idxmin()
            if not df.loc[idx, 'is_slope']:
                df.loc[idx, 'target'] = 2
        except Exception:
            pass

    return df.dropna(subset=['prev_fuel', 'time_diff', 'fuel_net_15m'])


def detect_events(device_df, model=None):
    """Detect consolidated theft/filling events for dashboard/alerts."""
    df = add_rolling_features(device_df)
    if model is not None:
        available = [c for c in FEATURE_COLS if c in df.columns]
        df = df.dropna(subset=available)
        if df.empty:
            return []
        df['target'] = model.predict(df[available])
    else:
        df['target'] = df.apply(classify_row, axis=1)

    events = []
    for device_id in df['deviceInfoId'].unique():
        dev = df[df['deviceInfoId'] == device_id].sort_values('deviceDataInsertTime')
        events.extend(_extract_events_from_device(dev))

    events.sort(key=lambda e: e['eventTime'])
    return events


def _extract_events_from_device(dev):
    """Merge consecutive predictions into single events."""
    events = []
    i = 0
    rows = dev.reset_index(drop=True)
    n = len(rows)

    while i < n:
        target = int(rows.loc[i, 'target'])
        if target == 0:
            i += 1
            continue

        start = i
        while i < n and int(rows.loc[i, 'target']) == target:
            i += 1
        end = i - 1

        window = rows.iloc[start:end + 1]
        event_type = 'Fuel Theft' if target == 1 else 'Fuel Filling'
        fuel_net = window['fuel_net_15m'].iloc[-1] if 'fuel_net_15m' in window else window['fuel_change'].sum()
        edc_net = window['edc_net_15m'].iloc[-1] if 'edc_net_15m' in window else window['edc_change'].sum()
        last = window.iloc[-1]

        if event_type == 'Fuel Theft' and last.get('is_slope', 0):
            continue
        if event_type == 'Fuel Filling' and last.get('is_slope', 0):
            continue
        if event_type == 'Fuel Theft' and abs(fuel_net) < 1 and abs(edc_net) < 5:
            continue
        if event_type == 'Fuel Filling' and fuel_net < FUEL_FILL_MIN:
            continue

        evt = {
            'eventType': event_type,
            'deviceInfoId': last.get('deviceInfoId'),
            'deviceName': last.get('deviceName', ''),
            'imei': last.get('imei', ''),
            'eventTime': last['deviceDataInsertTime'].isoformat(),
            'fuelChange': round(float(fuel_net), 2),
            'edcChange': round(float(edc_net), 2),
            'fuelLevel': round(float(last['fuelInLiter']), 2),
            'latitude': last.get('latitude'),
            'longitude': last.get('longitude'),
            'location': f"{last.get('latitude')}, {last.get('longitude')}",
            'ignitionStatus': 'OFF' if last.get('iO_1', 0) == 0 else 'ON',
            'isSlope': bool(last.get('is_slope', 0)),
            'source': 'ML',
        }
        if event_type == 'Fuel Filling':
            evt['fuelIncrease'] = abs(evt['fuelChange'])
        else:
            evt['fuelDecrease'] = abs(evt['fuelChange'])
        events.append(evt)

    return events
