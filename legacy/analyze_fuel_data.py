
import json
import csv
from datetime import datetime, timedelta
import os


def is_short_window_event(change, elapsed_minutes, event_type):
    if event_type == "theft":
        return change < -5 and elapsed_minutes <= 15
    if event_type == "filling":
        return change > 5 and elapsed_minutes <= 15
    return False


def load_data(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)


def parse_data(data):
    parsed_entries = []
    for entry in data:
        try:
            time_str = entry["deviceDataInsertTime"]
            if '.' in time_str:
                parts = time_str.split('.')
                if len(parts[1]) < 3:
                    parts[1] = parts[1].ljust(3, '0')
                time_str = '.'.join(parts)
            parsed_entry = {
                "deviceInfoId": entry["deviceInfoId"],
                "deviceName": entry["deviceName"],
                "deviceDataInsertTime": datetime.fromisoformat(time_str),
                "fuelInLiter": float(entry["fuelInLiter"]) if entry["fuelInLiter"] else 0,
                "iO_1": int(entry["iO_1"]) if entry["iO_1"] else 0,
                "iO_2": int(entry["iO_2"]) if entry["iO_2"] else 0,
                "iO_66": int(entry["iO_66"]) if entry["iO_66"] else 0,
                "iO_67": int(entry["iO_67"]) if entry["iO_67"] else 0,
                "iO_9": int(entry["iO_9"]) if entry["iO_9"] else 0,
                "latitude": entry["latitude"],
                "longitude": entry["longitude"],
            }
            parsed_entries.append(parsed_entry)
        except Exception as e:
            print(f"Error parsing entry: {e}")
    return parsed_entries


def group_by_device(parsed_entries):
    device_groups = {}
    for entry in parsed_entries:
        key = entry["deviceInfoId"]
        if key not in device_groups:
            device_groups[key] = {
                "deviceName": entry["deviceName"],
                "entries": []
            }
        device_groups[key]["entries"].append(entry)
    
    for key in device_groups:
        device_groups[key]["entries"].sort(key=lambda x: x["deviceDataInsertTime"])
    
    return device_groups


def detect_events(device_groups):
    events = []
    
    for device_id, group in device_groups.items():
        entries = group["entries"]
        if len(entries) < 2:
            continue
        
        # First pass: detect all candidate events
        candidate_events = []
        used_indices = set()
        
        for i in range(1, len(entries)):
            if i in used_indices:
                continue
                
            current = entries[i]
            previous = entries[i-1]
            
            fuel_diff = current["fuelInLiter"] - previous["fuelInLiter"]
            
            # Fuel filling: >5L increase within a short window
            if fuel_diff > 5:
                total_increase = fuel_diff
                end_i = i
                look_ahead_time = current["deviceDataInsertTime"] + timedelta(minutes=15)
                
                for j in range(i+1, len(entries)):
                    future_entry = entries[j]
                    if future_entry["deviceDataInsertTime"] > look_ahead_time:
                        break
                    next_diff = future_entry["fuelInLiter"] - entries[j-1]["fuelInLiter"]
                    if next_diff > 0:
                        total_increase += next_diff
                        end_i = j
                    else:
                        break
                
                elapsed_minutes = (entries[end_i]["deviceDataInsertTime"] - previous["deviceDataInsertTime"]).total_seconds() / 60.0
                if is_short_window_event(total_increase, elapsed_minutes, "filling"):
                    candidate_events.append({
                        "type": "filling",
                        "deviceId": device_id,
                        "deviceName": group["deviceName"],
                        "eventTime": entries[end_i]["deviceDataInsertTime"],
                        "latitude": entries[end_i]["latitude"],
                        "longitude": entries[end_i]["longitude"],
                        "fuelChange": round(total_increase, 2),
                        "startIndex": i,
                        "endIndex": end_i
                    })
                    # Mark indices as used
                    for idx in range(i, end_i + 1):
                        used_indices.add(idx)
            
            # Fuel theft: >5L decrease within a short window
            elif fuel_diff < -5:
                initial_fuel = previous["fuelInLiter"]
                theft_candidate_time = current["deviceDataInsertTime"]
                look_ahead_time = theft_candidate_time + timedelta(minutes=15)
                
                is_slope_case = False
                min_fuel_in_window = current["fuelInLiter"]
                max_fuel_in_window = initial_fuel
                
                for j in range(i+1, len(entries)):
                    future_entry = entries[j]
                    if future_entry["deviceDataInsertTime"] > look_ahead_time:
                        break
                    
                    min_fuel_in_window = min(min_fuel_in_window, future_entry["fuelInLiter"])
                    max_fuel_in_window = max(max_fuel_in_window, future_entry["fuelInLiter"])
                    
                    # If fuel recovers to within 2L, it's a slope case
                    if abs(future_entry["fuelInLiter"] - initial_fuel) < 2:
                        is_slope_case = True
                        break
                
                # Also check if fuel fluctuates a lot (slope)
                if (max_fuel_in_window - min_fuel_in_window) < 3:
                    is_slope_case = True
                
                elapsed_minutes = (current["deviceDataInsertTime"] - previous["deviceDataInsertTime"]).total_seconds() / 60.0
                if not is_slope_case:
                    total_decrease = initial_fuel - min_fuel_in_window
                    if is_short_window_event(-total_decrease, elapsed_minutes, "theft"):
                        candidate_events.append({
                            "type": "theft",
                            "deviceId": device_id,
                            "deviceName": group["deviceName"],
                            "eventTime": theft_candidate_time,
                            "latitude": current["latitude"],
                            "longitude": current["longitude"],
                            "fuelChange": round(total_decrease, 2),
                            "startIndex": i
                        })
                        used_indices.add(i)
        
        # Merge filling events that are close together (within 15 minutes)
        merged_filling_events = []
        if candidate_events:
            # Separate filling and theft
            filling_candidates = [e for e in candidate_events if e["type"] == "filling"]
            theft_candidates = [e for e in candidate_events if e["type"] == "theft"]
            
            # Merge fillings
            if filling_candidates:
                current_merge_group = [filling_candidates[0]]
                for candidate in filling_candidates[1:]:
                    last_event = current_merge_group[-1]
                    time_diff = (candidate["eventTime"] - last_event["eventTime"]).total_seconds()
                    if time_diff < 900:  # 15 minutes
                        current_merge_group.append(candidate)
                    else:
                        merged_filling_events.append(current_merge_group)
                        current_merge_group = [candidate]
                merged_filling_events.append(current_merge_group)
            
            # Process merged filling groups
            for group in merged_filling_events:
                total_increase = sum(e["fuelChange"] for e in group)
                last_event = group[-1]
                events.append({
                    "eventType": "Fuel Filling",
                    "deviceId": last_event["deviceId"],
                    "deviceName": last_event["deviceName"],
                    "eventTime": last_event["eventTime"].isoformat(),
                    "location": f"{last_event['latitude']}, {last_event['longitude']}",
                    "fuelIncrease": round(total_increase, 2),
                    "latitude": last_event["latitude"],
                    "longitude": last_event["longitude"],
                    "ignitionStatus": "ON" if last_event.get("ignitionStatus", "OFF") == "ON" else "OFF"
                })
            
            # Add theft events
            for event in theft_candidates:
                events.append({
                    "eventType": "Fuel Theft",
                    "deviceId": event["deviceId"],
                    "deviceName": event["deviceName"],
                    "eventTime": event["eventTime"].isoformat(),
                    "location": f"{event['latitude']}, {event['longitude']}",
                    "fuelDecrease": event["fuelChange"],
                    "latitude": event["latitude"],
                    "longitude": event["longitude"],
                    "ignitionStatus": "ON" if event.get("ignitionStatus", "OFF") == "ON" else "OFF"
                })
    
    # Sort all events by time
    events.sort(key=lambda x: x["eventTime"])
    return events


def save_events(events, output_dir):
    json_path = os.path.join(output_dir, "fuel_events.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(events, f, ensure_ascii=False, indent=4)
    print(f"Events saved to {json_path}")
    
    if events:
        csv_path = os.path.join(output_dir, "fuel_events.csv")
        all_keys = set()
        for event in events:
            all_keys.update(event.keys())
        
        ordered_keys = [
            "eventType", 
            "deviceId", 
            "deviceName", 
            "eventTime", 
            "location", 
            "ignitionStatus",
            "timeTaken"
        ]
        
        for key in sorted(all_keys - set(ordered_keys)):
            ordered_keys.append(key)
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=ordered_keys, restval='')
            writer.writeheader()
            writer.writerows(events)
        print(f"Events saved to {csv_path}")

    summary_rows = []
    for event in events:
        event_time = datetime.fromisoformat(event["eventTime"])
        summary_rows.append({
            "date": event_time.strftime("%Y-%m-%d"),
            "month": event_time.strftime("%Y-%m"),
            "deviceName": event["deviceName"],
            "eventType": event["eventType"],
            "volume": event.get("fuelIncrease", event.get("fuelDecrease", 0)),
            "eventTime": event["eventTime"],
        })

    if summary_rows:
        day_csv_path = os.path.join(output_dir, "fuel_event_summary_day.csv")
        month_csv_path = os.path.join(output_dir, "fuel_event_summary_month.csv")

        day_summary = {}
        for row in summary_rows:
            key = (row["date"], row["deviceName"], row["eventType"])
            day_summary.setdefault(key, {"date": row["date"], "deviceName": row["deviceName"], "eventType": row["eventType"], "volume": 0.0, "events": 0})
            day_summary[key]["volume"] += float(row["volume"])
            day_summary[key]["events"] += 1

        month_summary = {}
        for row in summary_rows:
            key = (row["month"], row["deviceName"], row["eventType"])
            month_summary.setdefault(key, {"month": row["month"], "deviceName": row["deviceName"], "eventType": row["eventType"], "volume": 0.0, "events": 0})
            month_summary[key]["volume"] += float(row["volume"])
            month_summary[key]["events"] += 1

        with open(day_csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=["date", "deviceName", "eventType", "volume", "events"], restval='')
            writer.writeheader()
            writer.writerows(sorted(day_summary.values(), key=lambda x: (x["date"], x["deviceName"], x["eventType"])))
        print(f"Day summary saved to {day_csv_path}")

        with open(month_csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=["month", "deviceName", "eventType", "volume", "events"], restval='')
            writer.writeheader()
            writer.writerows(sorted(month_summary.values(), key=lambda x: (x["month"], x["deviceName"], x["eventType"])))
        print(f"Month summary saved to {month_csv_path}")


def main():
    output_dir = "fuel_data"
    candidates = [
        os.path.join(output_dir, "june_2026_data.json"),
        os.path.join(output_dir, "live_fuel_data.json"),
        os.path.join(output_dir, "all_fuel_data_combined.json"),
    ]
    all_data_path = next((p for p in candidates if os.path.exists(p)), None)

    if not all_data_path:
        print("No fuel data file found! Run: python fetch_live_data.py")
        return
    
    print("Loading and parsing data...")
    data = load_data(all_data_path)
    parsed_entries = parse_data(data)
    print(f"Parsed {len(parsed_entries)} entries")
    
    print("Grouping by device...")
    device_groups = group_by_device(parsed_entries)
    print(f"Grouped into {len(device_groups)} devices")
    
    print("Detecting events...")
    events = detect_events(device_groups)
    print(f"Detected {len(events)} events!")
    
    if events:
        for event in events:
            print(f"\n{event['eventType']} detected for {event['deviceName']}:")
            print(f"  Time: {event['eventTime']}")
            print(f"  Location: {event['location']}")
    
    print("\nSaving events...")
    save_events(events, output_dir)


if __name__ == "__main__":
    main()

