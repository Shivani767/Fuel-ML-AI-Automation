
import requests
import csv
import json
import os
from datetime import datetime, timedelta

from env import load_env

# Credentials live in .env (gitignored), not in source.
load_env()

API_URL = os.environ.get(
    "FUELARK_API_URL",
    "https://api1.fuelark.com/api/FuelConsumption/DeviceFuelConsumption",
)
API_KEY = os.environ.get("FUELARK_API_KEY", "")

# List of 100 IMEIs provided by the user
IMEIS = [
    "350317173155320",
    "350317173167416",
    "350317176632549",
    "350317177063108",
    "350317177293978",
    "350424065768192",
    "350424065768796",
    "350544504907117",
    "350544504916241",
    "350544508026559",
    "350544508061697",
    "350544508070722",
    "350544508624916",
    "350544508625236",
    "350544508625244",
    "350612070117245",
    "350612071148249",
    "350612072452392",
    "350612072452640",
    "350612072489535",
    "350612072490095",
    "350612075179117",
    "350612075651156",
    "350612075838779",
    "350612076207636",
    "350612076207784",
    "352016700097967",
    "352016702577107",
    "352016702577404",
    "352016703275230",
    "352016705298362",
    "352016705582179",
    "352016708120654",
    "352016708120720",
    "352016708561311",
    "352016709015143",
    "352016709015259",
    "352093080607665",
    "352592572607356",
    "352592572623403",
    "352592572635605",
    "352592572734309",
    "352592573935897",
    "352592573935905",
    "352592573936028",
    "352592573936085",
    "352592574013421",
    "352625694767854",
    "353201350314552",
    "353201353323899",
    "353201353324046",
    "353201355168870",
    "353201355188464",
    "353201355505287",
    "353201355505550",
    "353201358650304",
    "353691840944119",
    "353691840959422",
    "353691841419327",
    "353691841533341",
    "353691842777988",
    "353691842819830",
    "353691843020610",
    "353691843255513",
    "353691843273268",
    "353691844388834",
    "353691844388867",
    "353691844409168",
    "353691844459528",
    "353691844459536",
    "353691844459601",
    "353691844459684",
    "353691844460617",
    "353691844715101",
    "353691845081594",
    "353691845089324",
    "353691845089464",
    "353691845099893",
    "353691845112183",
    "353691845112290",
    "353691846139722",
    "353691846818481",
    "353691846833860",
    "353691846913415",
    "353691847276937",
    "353691849317713",
    "353691849461768",
    "353691849464697",
    "353691849906747",
    "353691849945331",
    "353742371597733",
    "354017114715473",
    "354017118644026",
    "354018113563484",
    "357073290179817",
    "357073291510218",
    "357073297693539",
    "357544376206757",
    "359632106518308",
    "359633103253543",
    "357544374929160"
]

# June dates since existing data is from June 2026
start_date = datetime(2026, 6, 26)
end_date = datetime(2026, 6, 28)


def fetch_fuel_data(imei, date_str):
    from_datetime = f"{date_str} 00:00:00"
    to_datetime = f"{date_str} 23:59:59"
    
    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/json"
    }
    
    params = {
        "fromdate": from_datetime,
        "todate": to_datetime,
        "IMEI": imei
    }
    
    try:
        print(f"Fetching data for IMEI {imei} on {date_str}...")
        response = requests.get(API_URL, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        response_json = response.json()
        
        # Check response structure
        if "statusCode" in response_json and response_json["statusCode"] == 1 and "responseData" in response_json:
            return response_json["responseData"].get("deviceFuelInfo", [])
        elif "StatusCode" in response_json and response_json["StatusCode"] == 200 and "ResponseData" in response_json:
            return response_json["ResponseData"].get("DeviceFuelInfo", [])
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data for IMEI {imei} on {date_str}: {e}")
        return []


def save_to_csv(data, filename):
    if not data:
        return
    if isinstance(data, list) and len(data) > 0:
        keys = data[0].keys()
        with open(filename, 'w', newline='', encoding='utf-8') as output_file:
            dict_writer = csv.DictWriter(output_file, fieldnames=keys)
            dict_writer.writeheader()
            dict_writer.writerows(data)
        print(f"Data saved to {filename}")


def save_to_json(data, filename):
    if not data:
        return
    with open(filename, 'w', encoding='utf-8') as output_file:
        json.dump(data, output_file, ensure_ascii=False, indent=4)
    print(f"Data saved to {filename}")


def main():
    output_dir = "fuel_data"
    os.makedirs(output_dir, exist_ok=True)
    
    all_data = []
    
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        date_suffix = current_date.strftime("%Y%m%d")
        
        for imei in IMEIS:
            data = fetch_fuel_data(imei, date_str)
            if data:
                all_data.extend(data)
        
        current_date += timedelta(days=1)
    
    if all_data:
        all_json_filename = os.path.join(output_dir, "live_fuel_data.json")
        save_to_json(all_data, all_json_filename)
        print(f"\nAll data saved to {all_json_filename}")
        print(f"Total data points: {len(all_data)}")
    else:
        print("No data fetched.")


if __name__ == "__main__":
    main()
