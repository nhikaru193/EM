import time
import math
import RPi.GPIO as GPIO
from motor import MotorDriver  # ユーザーのMotorDriverクラスを使用
from BNO055 import BNO055
import smbus
import struct
import serial
import pigpio
import following
#緯度経度の取得
"""
def get_current_location():
    start_time = time.time()
    while time.time() - start_time < 3:
        (count, data) = pi.bb_serial_read(RX_PIN)
        if count and data:
            try:
                text = data.decode("ascii", errors="ignore")
                if "$GNRMC" in text:
                    lines = text.split("\n")
                    for line in lines:
                        if "$GNRMC" in line:
                            parts = line.strip().split(",")
                            if len(parts) > 6 and parts[2] == "A":
                                lat = convert_to_decimal(parts[3], parts[4])
                                lon = convert_to_decimal(parts[5], parts[6])
                                return lat, lon
            except Exception as e:
                print("デコードエラー:", e)
            time.sleep(0.1)
    return None

def convert_to_decimal(coord, direction):
    if direction in ['N', 'S']:
        degrees = int(coord[:2])
        minutes = float(coord[2:])
    else:
        degrees = int(coord[:3])
        minutes = float(coord[3:])
    decimal = degrees + minutes / 60.0
    if direction in ['S', 'W']:
        decimal *= -1
    return decimal

#2地点間の距離計測
def get_distance_ll(a, b):
    lat1, lon1 = a[0], a[1]
    lat2, lon2 = b[0], b[1]
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    s = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(s), math.sqrt(1 - s))
    return EARTH_RADIUS * c

def speed_test(duty):
    print(f"デューティ比{duty}まで加速中です..")
    driver.changing_forward(0, duty)
    print(f"デューティ比{duty}まで加速完了 + 距離計測を開始します")
    Departure_point = get_current_location()
    time.sleep(10)
    print("距離計測終了 + 減速を開始します")
    Arrival_point = get_current_location()
    driver.changing_forward(duty, 0)
    dist = get_distance_ll(Departure_point, Arrival_point)
    average = dist / 10
    print(f"計測終了です。デューティ比{duty}において")
    print(f"移動距離は{dist} m です")
    print(f"平均速度は{average} m/s です")
"""
#モータの初期設定
driver = MotorDriver(
    PWMA=12, AIN1=23, AIN2=18,    # 左モーター
    PWMB=19, BIN1=16, BIN2=26,    # 右モーター
    STBY=21
)

bno = BNO055()
if not bno.begin():
    print("Error initializing device")
    exit()
time.sleep(1)
bno.setMode(BNO055.OPERATION_MODE_NDOF)
time.sleep(1)
bno.setExternalCrystalUse(True)
"""
# === GPSデータ取得（仮の実装）===
TX_PIN = 17
RX_PIN = 27
BAUD = 9600

pi = pigpio.pi()
if not pi.connected:
    print("pigpio デーモンに接続できません。")
    exit(1)

err = pi.bb_serial_read_open(RX_PIN, BAUD, 8)
if err != 0:
    print(f"ソフトUART RX の設定に失敗：GPIO={RX_PIN}, {BAUD}bps")
    pi.stop()
    exit(1)

print(f"▶ ソフトUART RX を開始：GPIO={RX_PIN}, {BAUD}bps")
"""
#for i in range (1, 3):
#following.follow_petit_forward(driver, bno, 90, 0.5)
#following.follow_petit_forward(driver, bno, 90, 0.5)
#driver.petit_forward(0, 90)
#driver.petit_forward(90, 0)
#driver.petit_right(0, 90)
#driver.petit_right(90, 0)
#time.sleep(3.0)
#driver.petit_right(90, 0)
#driver.petit_left(0, 90)
#driver.petit_left(90, 0)
following.follow_forward(driver, bno, 80, 10)
driver.cleanup()
