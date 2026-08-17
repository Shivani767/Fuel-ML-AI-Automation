import json
import os
from datetime import datetime


def build_verified_theft_events(raw_data):
    verified_theft_events = []
    for sno, device_name, imei, date_str, qty in raw_data:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            try:
                day, month, year = date_str.split('/')
                year = "20" + year
                dt = datetime(int(year), int(month), int(day))
            except Exception:
                dt = datetime.now()

        event = {
            "eventType": "Fuel Theft",
            "deviceId": int(imei) if imei else 0,
            "deviceName": device_name.strip(),
            "eventTime": dt.isoformat(),
            "location": "",
            "fuelDecrease": qty,
            "latitude": "",
            "longitude": "",
            "ignitionStatus": "OFF",
            "verified": True,
        }
        verified_theft_events.append(event)

    return verified_theft_events


def merge_verified_events_into_event_feed(verified_events, events_path="fuel_data/fuel_events.json"):
    if os.path.exists(events_path):
        with open(events_path, "r", encoding="utf-8") as f:
            events = json.load(f)
    else:
        events = []

    existing_keys = {(event.get("deviceName"), event.get("eventTime"), event.get("eventType")) for event in events}
    added_events = []

    for event in verified_events:
        key = (event.get("deviceName"), event.get("eventTime"), event.get("eventType"))
        if key not in existing_keys:
            events.append(event)
            added_events.append(event)
            existing_keys.add(key)

    with open(events_path, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=4)

    return events, added_events


# Parse the user's provided data
raw_data = [
    (1, "Kamdhenu FBD TM HR-55-P-8009", 357544000000000, "2026-06-01", 2.34),
    (2, "SKS Gorakhpur UP-80-HM-3534", 353742000000000, "2026-06-02", 41.95),
    (3, "VIPL/BR3/TRAILER/2011/05 BR-26-GC-1447", 353692000000000, "2026-06-03", 14.02),
    (4, "VIPL/GTP/CAR/2022/06 UP-44-BJ-4486", 353692000000000, "2026-06-04", 2.05),
    (5, "Ceigall LBG TM PB-10-HH-7081", 353692000000000, "2026-06-03", 4.15),
    (6, "Gawar HIM L-05 HR-20-AH-7350", 353692000000000, "2026-06-03", 3.09),
    (7, "Ceigall LBG TM PB-10-HH-7081", 353692000000000, "2026-06-04", 7.26),
    (8, "Ceigall LBG TM PB-10-HJ-2981", 350317000000000, "2026-06-03", 8.15),
    (9, "Ceigall LBG TM PB-10-HJ-2981", 350317000000000, "2026-06-04", 8.33),
    (10, "Ceigall LBG TM PB-10-HJ-2981", 350317000000000, "2026-06-04", 7.29),
    (11, "Gawar Mum Hydra MH-46-CV-4414", 353692000000000, "2026-06-03", 2.02),
    (12, "Gawar Mum Paver No 20 (1800-3)", 353692000000000, "2026-06-05", 21.27),
    (13, "Gawar HIM EX-43 SR-YQ12178", 353692000000000, "2026-06-05", 65.96),
    (14, "Ceigall LBG TIP HR-65-A-9190", 353692000000000, "2026-06-05", 29.22),
    (15, "Ceigall LBG TIP PB-10-JT-7834", 353692000000000, "2026-06-05", 53.9),
    (16, "Excavator-210 Ananta Sany", 357073000000000, "2026-06-05", 139.92),
    (17, "Kamdhenu Sohana HR-55-AQ-7995 TM", 352017000000000, "2026-06-05", 46.5),
    (18, "Kamdhenu Sohana TM HR-55-AF-6459", 352626000000000, "2026-06-06", 130.78),
    (19, "Kamdhenu Sohana TM HR-55-AF-6459", 352626000000000, "2026-06-06", 81.51),
    (20, "Kamdhenu Sohana TM HR-55-AQ-9083", 350612000000000, "2026-06-05", 112.52),
    (21, "Kamdhenu Sohana TM HR-55-AQ-9083", 350612000000000, "2026-06-06", 30.64),
    (22, "RJ-14-GR-2657 Ananta TIP", 350612000000000, "2026-06-07", 25.67),
    (23, "ION-DevTech DG-100Kva", 359633000000000, "2026-06-07", 8.97),
    (24, "ION-DevTech DG-100Kva", 359633000000000, "2026-06-08", 45.06),
    (25, "Shiva Hyva RJ-14-GR-2593", 350612000000000, "2026-06-07", 51.6),
    (26, "SGI GUNA TM JK-17-A-2962", 353692000000000, "2026-06-09", 9.87),
    (27, "SGI GUNA DYNAPAC", 353692000000000, "2026-06-09", 21.78),
    (28, "SGI GUNA DYNAPAC", 353692000000000, "2026-06-09", 10.83),
    (29, "SGI GUNA Crusher Cone", 353692000000000, "2026-06-10", 19.05),
    (30, "Gawar Mum Excavator EX-76/YC06538", 353692000000000, "2026-06-09", 35.23),
    (31, "Gawar Mum HR-39-E-5960", 353201000000000, "2026-06-09", 2.66),
    (32, "Gawar Mum HR-39-E-5960", 353201000000000, "2026-06-10", 5.03),
    (33, "Krati Sany 210 C-9", 352017000000000, "2026-06-09", 29.76),
    (34, "Krati Sany 210 C-9", 352017000000000, "2026-06-10", 279.39),
    (35, "Krati Tipper MP-45-ZC-8873", 352593000000000, "2026-06-10", 28.33),
    (36, "Krati Tipper MP-45-ZC-8873", 352593000000000, "2026-06-10", 29.13),
    (37, "Krati Tipper MP-45-ZC-8873", 352593000000000, "2026-06-10", 30),
    (38, "RKC/TIPPER/103 HR-68-C-3144", 353692000000000, "2026-06-09", 3.28),
    (39, "Ceigall LBG TM PB-10-HJ-2981", 350317000000000, "2026-06-11", 2.09),
    (40, "Ceigall LBG TM PB-10-HJ-2981", 350317000000000, "2026-06-11", 2.84),
    (41, "RJ-14-GR-2655 Ananta Tipper", 354018000000000, "2026-06-10", 57.62),
    (42, "RJ-18-GB-7215 Ananta Trailer", 350545000000000, "2026-06-11", 187.94),
    (43, "Skylark Banglore DG51 DG-125kva (BOYD)", 350545000000000, "2026-06-10", 29.03),
    (44, "SGI GUNA TPR-21 MZ-06-9500", 353692000000000, "2026-06-11", 8.49),
    (45, "SGI GUNA TPR-21 MZ-06-9500", 353692000000000, "2026-06-11", 6.71),
    (46, "JSPL SMS Slag CG-13-AW-1214", 353692000000000, "2026-06-12", 168.22),
    (47, "RJ-14-GR-2655 Ananta Tipper", 354018000000000, "2026-06-11", 67.39),
    (48, "Skylark Akot HV31 HR-38-AD-4504 (New)", 353692000000000, "2026-06-12", 9.32),
    (49, "ION-DevTech DG-200Kva", 359633000000000, "2026-06-13", 44.22),
    (50, "JSPL SMS Slag CG-13-AW-1214", 353692000000000, "2026-06-13", 204.37),
    (51, "Shiva Hyva RJ 14 GQ 5746", 352017000000000, "2026-06-12", 29.08),
    (52, "Skylark Akot HB102 MH-30-BD-9131 (New)", 353692000000000, "2026-06-13", 33.68),
    (53, "Skylark Akot HV37 HR-38-AD-1230 (New)", 353692000000000, "2026-06-13", 3.18),
    (54, "Gawar HIM EX-75 SR-YQ13720", 353692000000000, "2026-06-15", 52.95),
    (55, "JSPL SMS Slag CG-13-AW-1214", 353692000000000, "2026-06-14", 133.6),
    (56, "Krati Tipper RJ-21-GA-9016", 352593000000000, "2026-06-15", 59.53),
    (57, "Rise-11-Agra Tractor HR-51-CN-2046", 353201000000000, "2026-06-15", 1.21),
    (58, "Gawar Mum HR-39-E-4702", 353692000000000, "2026-06-15", 244.67),
    (59, "Skylark Akot A/F-5 MH-30-BD-9254 (New)", 353692000000000, "2026-06-15", 10.63),
    (60, "ION-DevTech DG-100Kva", 359633000000000, "2026-06-16", 37.82),
    (61, "RJ-14-GR-2656 Ananta Tipper", 354017000000000, "2026-06-16", 33.7),
    (62, "ION-DevTech DG-100Kva", 359633000000000, "2026-06-17", 12.91),
    (63, "ION-DevTech DG-100Kva", 359633000000000, "2026-06-17", 44.83),
    (64, "Gawar Mum Hydra MH-46-CV-3406", 353692000000000, "2026-06-19", 36.63),
    (65, "JSPL DRI-1 CG-13-AF-5067", 353692000000000, "2026-06-19", 16.78),
    (66, "RoyalGen DG-62Kva HR-55-AN-8593", 358480000000000, "2026-06-20", 24.96),
    (67, "Skylark Akot EX11 YQ15511377 (BYOD)", 350545000000000, "2026-06-19", 5.05),
    (68, "SKS Raebareli Transit Mixer UP-83-AT-7394", 353201000000000, "2026-06-22", 2.02),
    (69, "Ceigall LBG TIP PB-10-JX-0618", 353692000000000, "2026-06-24", 66.82),
    (70, "Ceigall LBG TIP PB-10-JX-0618", 353692000000000, "2026-06-24", 26.75),
    (71, "Skylark Surat HC09 NL-01-DA-0138 (BOYD)", 350317000000000, "2026-06-25", 31.93),
    (72, "Gawar Mum EX-45 Excavator", 353692000000000, "2026-06-25", 4.71),
    (73, "Gawar HIM PTR Roller-26", 353692000000000, "2026-06-25", 3.78),
    (74, "Ceigall LBG TM PB-10-HH-7081", 353692000000000, "2026-06-29", 4.49),
    (75, "Ceigall LBG TM HR-65-B-2189", 353692000000000, "2026-06-27", 2.13),
    (76, "Ceigall LBG TM HR-65-B-2189", 353692000000000, "2026-06-28", 1.86),
    (77, "DevYash DHL TIP HR-58-E-3944", 353692000000000, "2026-06-29", 98.69),
    (78, "DevYash Meerut HR-58-E-0738", 353692000000000, "2026-06-27", 56.75),
    (79, "DevYash Meerut TIP UP-14-GT-9934", 353692000000000, "2026-06-27", 19.77),
    (80, "Kamdhenu DLI TM HR-55-AL-0751", 350424000000000, "2026-06-28", 28.34),
    (81, "Kamdhenu DLI TM HR-55-AL-1351", 350424000000000, "2026-06-28", 23.67),
    (82, "DMR Amritsar Kobelco Excavator 220", 353692000000000, "2026-06-29", 1.36)
]

# Convert to structured events
verified_theft_events = build_verified_theft_events(raw_data)

# Save to JSON
output_file = "fuel_data/verified_theft_events.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(verified_theft_events, f, ensure_ascii=False, indent=4)

merged_events, added_events = merge_verified_events_into_event_feed(
    verified_theft_events,
    "fuel_data/fuel_events.json",
)

print(f"Saved {len(verified_theft_events)} verified theft events to {output_file}")
print(f"Merged {len(added_events)} new verified theft entries into fuel_data/fuel_events.json")
