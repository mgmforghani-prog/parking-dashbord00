import numpy as np
import pandas as pd
from datetime import datetime, timedelta

def get_mock_data():
    np.random.seed(42)
    n_records = 200
    now = datetime.now()

    # شبیه‌سازی گیت‌ها
    gates = [
        {"GateId": 1, "GateName": "ورودی اصلی (شمال)", "GateType": "Entry"},
        {"GateId": 2, "GateName": "ورودی پرسنلی (غرب)", "GateType": "Entry"},
        {"GateId": 3, "GateName": "خروجی اصلی (جنوب)", "GateType": "Exit"},
        {"GateId": 4, "GateName": "خروجی پرسنلی (شرق)", "GateType": "Exit"}
    ]
    df_gates = pd.DataFrame(gates)

    # شبیه‌سازی لاگ پلاک‌خوان (ANPRPlates)
    plates_data = []
    plate_letters = ['ب', 'ج', 'د', 'س', 'ط', 'ق', 'ل', 'م', 'ن', 'ه', 'ی']
    
    for i in range(1, n_records + 1):
        rand_time = now - timedelta(minutes=np.random.randint(5, 720))
        plate_no = f"{np.random.randint(11,99)}{np.random.choice(plate_letters)}{np.random.randint(111,999)}-{np.random.randint(11,99)}"
        conf = np.random.beta(8, 2)  # توزیع متمایل به دقت بالا (0.75 تا 0.99)
        if np.random.rand() < 0.05:  # چند مورد آنومالی با دقت پایین
            conf = np.random.uniform(0.2, 0.5)

        plates_data.append({
            "PlateId": i,
            "PlateNumber": plate_no,
            "Confidence": round(float(conf), 2),
            "CaptureTime": rand_time,
            "GateId": np.random.choice([1, 2, 3, 4]),
            "IsCorrected": np.random.choice([0, 1], p=[0.92, 0.08])
        })
    df_plates = pd.DataFrame(plates_data)

    # شبیه‌سازی ترددها (Taradods)
    taradod_data = []
    for i in range(1, n_records + 1):
        entry_time = now - timedelta(hours=np.random.randint(1, 48), minutes=np.random.randint(0, 59))
        duration_minutes = np.random.exponential(scale=120) + 15
        exit_time = entry_time + timedelta(minutes=duration_minutes) if np.random.rand() > 0.25 else None
        
        # محاسبه هزینه و وضعیت پرداخت
        fee = int(duration_minutes * 250) if exit_time else 0
        is_paid = 1 if exit_time and np.random.rand() > 0.08 else (0 if exit_time else 0)
        
        taradod_data.append({
            "TaradodId": i,
            "PlateNumber": df_plates.iloc[i-1]["PlateNumber"],
            "EntryTime": entry_time,
            "ExitTime": exit_time,
            "EntryGateId": np.random.choice([1, 2]),
            "ExitGateId": np.random.choice([3, 4]) if exit_time else None,
            "Fee": fee,
            "IsPaid": is_paid,
            "IsVIP": 1 if np.random.rand() < 0.1 else 0,
            "ManualOpen": 1 if np.random.rand() < 0.04 else 0  # بازگشایی دستی / تخلف مشکوک
        })
    df_taradod = pd.DataFrame(taradod_data)

    # شبیه‌سازی لاگ راهبند (BarrierLogs)
    barrier_logs = []
    for i in range(1, 100):
        b_time = now - timedelta(minutes=np.random.randint(10, 600))
        barrier_logs.append({
            "LogId": i,
            "GateId": np.random.choice([1, 2, 3, 4]),
            "ActionType": np.random.choice(["AutoOpen", "ManualButton", "SoftwareOverride", "LoopSensorError"], p=[0.75, 0.15, 0.06, 0.04]),
            "Timestamp": b_time,
            "ResponseTimeMs": int(np.random.normal(450, 80))
        })
    df_barrier = pd.DataFrame(barrier_logs)

    return {
        "taradods": df_taradod,
        "plates": df_plates,
        "gates": df_gates,
        "barriers": df_barrier
    }
