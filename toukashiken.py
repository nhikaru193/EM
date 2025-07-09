import cv2
import numpy as np
import time
from picamera2 import Picamera2
from motor import MotorDriver
import following
from BNO055 import BNO055
import smbus
import RPi.GPIO as GPIO
import pigpio
import board
import busio 
import os
import math
import sys

# --- 共通のBME280グローバル変数と関数 ---
t_fine = 0.0
digT = []
digP = []
digH = []

i2c = smbus.SMBus(1)
BME280_address = 0x76 # BME280のアドレス

def init_bme280():
    """BME280センサーを初期化します。"""
    i2c.write_byte_data(BME280_address, 0xF2, 0x01)
    i2c.write_byte_data(BME280_address, 0xF4, 0x27)
    i2c.write_byte_data(BME280_address, 0xF5, 0xA0)

def read_compensate():
    """BME280の補正パラメータを読み込みます。"""
    global digT, digP, digH
    dat_t = i2c.read_i2c_block_data(BME280_address, 0x88, 6)
    digT = [(dat_t[1] << 8) | dat_t[0], (dat_t[3] << 8) | dat_t[2], (dat_t[5] << 8) | dat_t[4]]
    for i in range(1, 2):
        if digT[i] >= 32768:
            digT[i] -= 65536
    dat_p = i2c.read_i2c_block_data(BME280_address, 0x8E, 18)
    digP = [(dat_p[i+1] << 8) | dat_p[i] for i in range(0, 18, 2)]
    for i in range(1, 8):
        if digP[i] >= 32768:
            digP[i] -= 65536
    dh = i2c.read_byte_data(BME280_address, 0xA1)
    dat_h = i2c.read_i2c_block_data(BME280_address, 0xE1, 8)
    digH = [dh, (dat_h[1] << 8) | dat_h[0], dat_h[2],
            (dat_h[3] << 4) | (0x0F & dat_h[4]),
            (dat_h[5] << 4) | ((dat_h[4] >> 4) & 0x0F),
            dat_h[6]]
    if digH[1] >= 32768:
        digH[1] -= 65536
    for i in range(3, 4):
        if digH[i] >= 32768:
            digH[i] -= 65536
    if digH[5] >= 128:
        digH[5] -= 256

def bme280_compensate_t(adc_T):
    """BME280の温度値を補正します。"""
    global t_fine
    var1 = (adc_T / 8.0 - digT[0] * 2.0) * digT[1] / 2048.0
    var2 = ((adc_T / 16.0 - digT[0]) ** 2) * digT[2] / 16384.0
    t_fine = var1 + var2
    t = (t_fine * 5 + 128) / 256 / 100
    return t

def bme280_compensate_p(adc_P):
    """BME280の気圧値を補正します。"""
    global t_fine
    p = 0.0 # BME280の元のコードではpの初期化がなかったため追加
    var1 = t_fine - 128000.0
    var2 = var1 * var1 * digP[5]
    var2 += (var1 * digP[4]) * 131072.0
    var2 += digP[3] * 3.435973837e10
    var1 = (var1 * var1 * digP[2]) / 256.0 + (var1 * digP[1]) * 4096
    var1 = (1.407374884e14 + var1) * (digP[0] / 8589934592.0)
    if var1 == 0:
        return 0
    p = (1048576.0 - adc_P) * 2147483648.0 - var2
    p = (p * 3125) / var1
    var1 = digP[8] * (p / 8192.0)**2 / 33554432.0
    var2 = digP[7] * p / 524288.0
    p = (p + var1 + var2) / 256 + digP[6] * 16.0
    return p / 256 / 100

def get_pressure_and_temperature():
    """BME280から気圧と温度を読み込み、補正して返します。"""
    dat = i2c.read_i2c_block_data(BME280_address, 0xF7, 8)
    adc_p = (dat[0] << 16 | dat[1] << 8 | dat[2]) >> 4
    adc_t = (dat[3] << 16 | dat[4] << 8 | dat[5]) >> 4
    
    temperature = bme280_compensate_t(adc_t)
    pressure = bme280_compensate_p(adc_p)
    return pressure, temperature

# --- 1. 放出判定用の関数 ---

def check_release(bno_sensor_instance, pressure_change_threshold=0.3, acc_z_threshold_abs=4.0, consecutive_checks=3, timeout=60):
    """
    放出判定を行う関数。BME280の気圧変化とBNO055のZ軸加速度を監視します。
    """
    init_bme280()
    read_compensate()

    if not bno_sensor_instance.begin():
        print("🔴 BNO055 初期化失敗。放出判定を中止します。")
        return False

    bno_sensor_instance.setExternalCrystalUse(True)
    bno_sensor_instance.setMode(BNO055.OPERATION_MODE_NDOF)
    print("\n⚠️ BNO055 キャリブレーションはスキップされました。線形加速度の精度が低下する可能性があります。")

    print("\n🚀 放出判定開始...")
    print(f"    初期気圧からの変化量閾値: >= {pressure_change_threshold:.2f} hPa")
    print(f"    Z軸加速度絶対値閾値: > {acc_z_threshold_abs:.2f} m/s²")
    print(f"    連続成立回数: {consecutive_checks}回")
    print(f"    タイムアウト: {timeout}秒\n")

    release_count = 0
    start_time = time.time()
    last_check_time = time.time()
    initial_pressure = None

    try:
        print(f"{'Timestamp(s)':<15}{'Elapsed(s)':<12}{'Current_P(hPa)':<15}{'Initial_P(hPa)':<15}{'P_Chg(hPa)':<15}{'Acc_Z(m/s2)':<12}")
        print("-" * 100)

        while True:
            current_time = time.time()
            elapsed_total = current_time - start_time

            if elapsed_total > timeout:
                print(f"\n⏰ タイムアウト ({timeout}秒経過)。放出判定を失敗とします。")
                return False
            
            if (current_time - last_check_time) < 0.2:
                time.sleep(0.01)
                continue
            
            last_check_time = current_time

            current_pressure, _ = get_pressure_and_temperature()
            _, _, acc_z = bno_sensor_instance.getVector(BNO055.VECTOR_LINEARACCEL)

            if initial_pressure is None:
                initial_pressure = current_pressure
                print(f"{current_time:<15.3f}{elapsed_total:<12.1f}{current_pressure:<15.2f}{initial_pressure:<15.2f}{'-':<15}{acc_z:<12.2f}")
                print("\n--- 初期気圧設定完了。放出条件監視中... ---")
                continue

            pressure_delta_from_initial = abs(current_pressure - initial_pressure)
            
            print(f"{current_time:<15.3f}{elapsed_total:<12.1f}{current_pressure:<15.2f}{initial_pressure:<15.2f}{pressure_delta_from_initial:<15.2f}{acc_z:<12.2f}")

            is_release_condition_met = (
                pressure_delta_from_initial >= pressure_change_threshold and
                abs(acc_z) > acc_z_threshold_abs
            )

            if is_release_condition_met:
                release_count += 1
                print(f"\n💡 条件成立！連続判定中: {release_count}/{consecutive_checks} 回")
            else:
                if release_count > 0:
                    print(f"\n--- 条件不成立。カウントリセット ({release_count} -> 0) ---")
                release_count = 0

            if release_count >= consecutive_checks:
                print(f"\n🎉 放出判定成功！連続 {consecutive_checks} 回条件成立！")
                return True

    except KeyboardInterrupt:
        print(f"\n{current_time:<15.3f}{elapsed_total:<12.1f}{current_pressure:<15.2f}{initial_pressure:<15.2f}{pressure_delta_from_initial:<15.2f}{acc_z:<12.2f}")
        print("\n\nプログラムがユーザーによって中断されました。")
        return False
    except Exception as e:
        print(f"\n{current_time:<15.3f}{elapsed_total:<12.1f}{current_pressure:<15.2f}{initial_pressure:<15.2f}{pressure_delta_from_initial:<15.2f}{acc_z:<12.2f}")
        print(f"\n\n🚨 エラーが発生しました: {e}")
        return False
    finally:
        print("\n--- 放出判定処理終了 ---")


# --- 2. 着地判定用の関数 ---

def check_landing(bno_sensor_instance, pressure_change_threshold=0.1, acc_threshold_abs=0.5, gyro_threshold_abs=0.5, consecutive_checks=3, timeout=60, calibrate_bno055=True):
    """
    着地判定を行う関数。気圧の変化量、加速度、角速度が閾値内に収まる状態を監視します。
    """
    init_bme280()
    read_compensate()

    if not bno_sensor_instance.begin():
        print("🔴 BNO055 初期化失敗。着地判定を中止します。")
        return False

    bno_sensor_instance.setExternalCrystalUse(True)
    bno_sensor_instance.setMode(BNO055.OPERATION_MODE_NDOF)

    if calibrate_bno055:
        print("\n⚙️ BNO055 キャリブレーション中... センサーをいろんな向きにゆっくり回してください。")
        print("    (ジャイロ、地磁気が完全キャリブレーション(レベル3)になるのを待ちます)")
        calibration_start_time = time.time()
        while True:
            sys_cal, gyro_cal, accel_cal, mag_cal = bno_sensor_instance.getCalibration()
            print(f"    現在のキャリブレーション状態 → システム:{sys_cal}, ジャイロ:{gyro_cal}, 加速度:{acc_cal}, 地磁気:{mag_cal} ", end='\r')
            
            if gyro_cal == 3 and mag_cal == 3: # 加速度もレベル3を待つように変更
                print("\n✅ BNO055 キャリブレーション完了！")
                break
            time.sleep(0.5)
        print(f"    キャリブレーションにかかった時間: {time.time() - calibration_start_time:.1f}秒\n")
    else:
        print("\n⚠️ BNO055 キャリブレーション待機はスキップされました。")

    print("🛬 着地判定開始...")
    print(f"    気圧変化量閾値: < {pressure_change_threshold:.2f} hPa")
    print(f"    加速度絶対値閾値: < {acc_threshold_abs:.2f} m/s² (X, Y, Z軸)")
    print(f"    角速度絶対値閾値: < {gyro_threshold_abs:.2f} °/s (X, Y, Z軸)")
    print(f"    連続成立回数: {consecutive_checks}回")
    print(f"    タイムアウト: {timeout}秒\n")

    landing_count = 0
    start_time = time.time()
    last_check_time = time.time()
    previous_pressure = None

    try:
        print(f"{'Timestamp(s)':<15}{'Elapsed(s)':<12}{'Pressure(hPa)':<15}{'Pressure_Chg(hPa)':<18}{'Acc_X':<8}{'Acc_Y':<8}{'Acc_Z':<8}{'Gyro_X':<8}{'Gyro_Y':<8}{'Gyro_Z':<8}")
        print("-" * 120)

        while True:
            current_time = time.time()
            elapsed_total = current_time - start_time

            if elapsed_total > timeout:
                print(f"\n⏰ タイムアウト ({timeout}秒経過)。条件成立回数 {landing_count} 回でしたが、強制的に着地判定を成功とします。")
                return True
            
            if (current_time - last_check_time) < 0.2:
                time.sleep(0.01)
                continue
            
            last_check_time = current_time

            current_pressure, _ = get_pressure_and_temperature()
            acc_x, acc_y, acc_z = bno_sensor_instance.getVector(BNO055.VECTOR_LINEARACCEL)
            gyro_x, gyro_y, gyro_z = bno_sensor_instance.getVector(BNO055.VECTOR_GYROSCOPE)

            pressure_delta = float('inf')
            if previous_pressure is not None:
                pressure_delta = abs(current_pressure - previous_pressure)
            
            print(f"{current_time:<15.3f}{elapsed_total:<12.1f}{current_pressure:<15.2f}{pressure_delta:<18.2f}{acc_x:<8.2f}{acc_y:<8.2f}{acc_z:<8.2f}{gyro_x:<8.2f}{gyro_y:<8.2f}{gyro_z:<8.2f}", end='\r')

            is_landing_condition_met = (
                pressure_delta <= pressure_change_threshold and
                abs(acc_x) < acc_threshold_abs and
                abs(acc_y) < acc_threshold_abs and
                abs(acc_z) < acc_threshold_abs and
                abs(gyro_x) < gyro_threshold_abs and
                abs(gyro_y) < gyro_threshold_abs and
                abs(gyro_z) < gyro_threshold_abs
            )

            previous_pressure = current_pressure

            if is_landing_condition_met:
                landing_count += 1
                print(f"\n💡 条件成立！連続判定中: {landing_count}/{consecutive_checks} 回")
            else:
                if landing_count > 0:
                    print(f"\n--- 条件不成立。カウントリセット ({landing_count} -> 0) ---")
                landing_count = 0

            if landing_count >= consecutive_checks:
                print(f"\n🎉 着地判定成功！連続 {consecutive_checks} 回条件成立！")
                return True

    except KeyboardInterrupt:
        print("\n\nプログラムがユーザーによって中断されました。")
        return False
    except Exception as e:
        print(f"\n\n🚨 エラーが発生しました: {e}")
        return False
    finally:
        print("\n--- 判定処理終了 ---")


# --- 3. 赤色物体追跡と接近用の関数群 (修正版) ---

def get_percentage_approach(picam2_instance):
    """
    カメラフレーム内の赤いピクセルの割合を計算します。(接近用)
    """
    frame = picam2_instance.capture_array()
    frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) # Picamera2はRGBを返すため、BGRに変換
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    frame = cv2.GaussianBlur(frame, (5, 5), 0)

    lower_red1 = np.array([0, 100, 100])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 100, 100])
    upper_red2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask = cv2.bitwise_or(mask1, mask2)

    red_area = np.count_nonzero(mask)
    total_area = frame.shape[0] * frame.shape[1]
    percentage = (red_area / total_area) * 100
    return percentage

def get_block_number_approach(picam2_instance):
    """
    赤色物体の重心が画面のどのブロック（左から1〜5）に当たるかを計算します。(接近用)
    """
    number = None
    frame = picam2_instance.capture_array()
    frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) # Picamera2はRGBを返すため、BGRに変換
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    frame = cv2.GaussianBlur(frame, (5, 5), 0)

    lower_red1 = np.array([0, 100, 50])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([170, 100, 50])
    upper_red2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask = cv2.bitwise_or(mask1, mask2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if contours:
        largest = max(contours, key=cv2.contourArea)
        M = cv2.moments(largest)
        if M["m00"] != 0:
            cx = int(M["m10"] / M["m00"])
            width = frame.shape[1]
            w = width // 5
            if cx < w:
                number = 1
            elif cx < 2 * w:
                number = 2
            elif cx < 3 * w:
                number = 3
            elif cx < 4 * w:
                number = 4
            else:
                number = 5
        else:
            print("⚠️ 重心が計算できません")
            number = None
    else:
        print("❌ 赤色物体が見つかりません")
        number = None
    return number


# --- 4. 自律走行（GPSとカメラによる障害物回避）用の関数群 ---

# 定数設定
destination_lat = 35.9248066
destination_lon = 139.9112360
RX_PIN = 17 # GPSデータ受信用ピン

# BNO055用のラッパークラス
class BNO055Wrapper:
    def __init__(self, bno055_sensor_instance):
        self.sensor = bno055_sensor_instance

    def get_heading(self):
        euler_angles = self.sensor.getEuler() 
        if euler_angles is None or euler_angles[0] is None:
            wait_start_time = time.time()
            max_wait_time = 0.1
            while (euler_angles is None or euler_angles[0] is None) and (time.time() - wait_start_time < max_wait_time):
                time.sleep(0.005)
                euler_angles = self.sensor.getEuler()
        
        if euler_angles is None or euler_angles[0] is None:
            return None
        
        heading = euler_angles[0]
        return heading


def convert_to_decimal(coord, direction):
    """NMEA形式のGPS座標を十進数に変換します。"""
    degrees = int(coord[:2]) if direction in ['N', 'S'] else int(coord[1:3]) # 緯度の度を2桁、経度を3桁で取得
    minutes = float(coord[2:]) if direction in ['N', 'S'] else float(coord[3:])
    decimal = degrees + minutes / 60
    if direction in ['S', 'W']:
        decimal *= -1
    return decimal

def get_current_location(pi_instance, rx_pin):
    """GPSデータから現在の緯度と経度を取得します。
        タイムアウトした場合、None, Noneを返します。
    """
    timeout = time.time() + 5
    while time.time() < timeout:
        (count, data) = pi_instance.bb_serial_read(rx_pin)
        if count and data:
            try:
                text = data.decode("ascii", errors="ignore")
                # $GPRMCまたは$GNRMCを処理
                for line in text.split("\n"):
                    if "$GPRMC" in line or "$GNRMC" in line:
                        parts = line.strip().split(",")
                        if len(parts) > 6 and parts[2] == "A": # "A"はデータ有効
                            lat = convert_to_decimal(parts[3], parts[4])
                            lon = convert_to_decimal(parts[5], parts[6])
                            return lat, lon
            except Exception as e:
                print(f"GPSデータ解析エラー: {e}")
                continue
        time.sleep(0.1)
    print("GPSデータの取得に失敗しました (タイムアウト)。")
    return None, None

def get_bearing_to_goal(current, goal):
    """現在の位置から目標位置への方位（度）を計算します。"""
    if current is None or goal is None: return None
    lat1, lon1 = math.radians(current[0]), math.radians(current[1])
    lat2, lon2 = math.radians(goal[0]), math.radians(goal[1])
    delta_lon = lon2 - lon1
    y = math.sin(delta_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    bearing_rad = math.atan2(y, x)
    return (math.degrees(bearing_rad) + 360) % 360

def get_distance_to_goal(current, goal):
    """現在の位置から目標位置までの距離（メートル）を計算します。"""
    if current is None or goal is None: return float('inf')
    lat1, lon1 = math.radians(current[0]), math.radians(current[1])
    lat2, lon2 = math.radians(goal[0]), math.radians(goal[1])
    radius = 6378137.0 # 地球の平均半径（メートル）
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    dist = radius * c
    return dist

def save_image_for_debug(picam2_instance, path="/home/mark1/Pictures/paravo_image.jpg"):
    """デバッグ用に画像を保存します。"""
    frame = picam2_instance.capture_array()
    if frame is None:
        print("画像キャプチャ失敗：フレームがNoneです。")
        return None
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) # Picamera2はRGBを返すのでBGRに変換
    cv2.imwrite(path, frame_bgr)
    print(f"画像保存成功: {path}")
    return frame

def detect_red_in_grid(picam2_instance, save_path="/home/mark1/Pictures/akairo_grid.jpg", min_red_pixel_ratio_per_cell=0.05):
    """
    カメラ画像を縦2x横3のグリッドに分割し、各セルでの赤色検出を行い、その位置情報を返します。
    """
    try:
        frame_rgb = picam2_instance.capture_array()
        if frame_rgb is None:
            print("画像キャプチャ失敗: フレームがNoneです。")
            return 'error_in_processing'

        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        
        # Picamera2のconfigureで回転を指定済みのため、ここでは反転のみを考慮
        processed_frame_bgr = cv2.flip(frame_bgr, 1) # 1は水平フリップ (左右反転)
        
        height, width, _ = processed_frame_bgr.shape
        cell_height = height // 2 ; cell_width = width // 3
        cells = {
            'top_left': (0, cell_height, 0, cell_width), 'top_middle': (0, cell_height, cell_width, 2 * cell_width),
            'top_right': (0, cell_height, 2 * cell_width, width),
            'bottom_left': (cell_height, height, 0, cell_width), 'bottom_middle': (cell_height, height, cell_width, 2 * cell_width),
            'bottom_right': (cell_height, height, 2 * cell_width, width),
        }
        red_counts = {key: 0 for key in cells} ; total_pixels_in_cell = {key: 0 for key in cells}

        lower_red1 = np.array([0, 100, 100]) ; upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([160, 100, 100]) ; upper_red2 = np.array([180, 255, 255])

        blurred_full_frame = cv2.GaussianBlur(processed_frame_bgr, (5, 5), 0)
        hsv_full = cv2.cvtColor(blurred_full_frame, cv2.COLOR_BGR2HSV)
        mask_full = cv2.bitwise_or(cv2.inRange(hsv_full, lower_red1, upper_red1),
                                     cv2.inRange(hsv_full, lower_red2, upper_red2))
        red_pixels_full = np.count_nonzero(mask_full) ; total_pixels_full = height * width
        red_percentage_full = red_pixels_full / total_pixels_full if total_pixels_full > 0 else 0.0

        if red_percentage_full >= 0.80:
            print(f"画像全体の赤色ピクセル割合: {red_percentage_full:.2%} (高割合) -> high_percentage_overall")
            cv2.imwrite(save_path, processed_frame_bgr)
            return 'high_percentage_overall'

        debug_frame = processed_frame_bgr.copy()
        for cell_name, (y_start, y_end, x_start, x_end) in cells.items():
            cell_frame = processed_frame_bgr[y_start:y_end, x_start:x_end]
            blurred_cell_frame = cv2.GaussianBlur(cell_frame, (5, 5), 0)
            hsv_cell = cv2.cvtColor(blurred_cell_frame, cv2.COLOR_BGR2HSV)
            mask_cell = cv2.bitwise_or(cv2.inRange(hsv_cell, lower_red1, upper_red1),
                                         cv2.inRange(hsv_cell, lower_red2, upper_red2))
            red_counts[cell_name] = np.count_nonzero(mask_cell)
            total_pixels_in_cell[cell_name] = cell_frame.shape[0] * cell_frame.shape[1]
            
            color = (255, 0, 0) ; thickness = 2
            if red_counts[cell_name] / total_pixels_in_cell[cell_name] >= min_red_pixel_ratio_per_cell:
                color = (0, 0, 255) ; thickness = 3
            cv2.rectangle(debug_frame, (x_start, y_start), (x_end, y_end), color, thickness)
            cv2.putText(debug_frame, f"{cell_name}: {(red_counts[cell_name] / total_pixels_in_cell[cell_name]):.2f}", 
                                     (x_start + 5, y_start + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        directory = os.path.dirname(save_path)
        if not os.path.exists(directory): os.makedirs(directory)
        cv2.imwrite(save_path, debug_frame)
        print(f"グリッド検出画像を保存しました: {save_path}")

        bottom_left_ratio = red_counts['bottom_left'] / total_pixels_in_cell['bottom_left']
        bottom_middle_ratio = red_counts['bottom_middle'] / total_pixels_in_cell['bottom_middle']
        bottom_right_ratio = red_counts['bottom_right'] / total_pixels_in_cell['bottom_right']

        detected_cells = []
        if bottom_left_ratio >= min_red_pixel_ratio_per_cell: detected_cells.append('bottom_left')
        if bottom_middle_ratio >= min_red_pixel_ratio_per_cell: detected_cells.append('bottom_middle')
        if bottom_right_ratio >= min_red_pixel_ratio_per_cell: detected_cells.append('bottom_right')

        if len(detected_cells) == 0:
            print("赤色を検出しませんでした (下段)")
            return 'none_detected'
        elif 'bottom_left' in detected_cells and 'bottom_right' not in detected_cells:
            print("赤色が左下に偏って検出されました")
            return 'left_bottom'
        elif 'bottom_right' in detected_cells and 'bottom_left' not in detected_cells:
            print("赤色が右下に偏って検出されました")
            return 'right_bottom'
        elif 'bottom_left' in detected_cells and 'bottom_middle' in detected_cells and 'bottom_right' in detected_cells:
            print("赤色が下段全体に広く検出されました")
            return 'bottom_middle'
        elif 'bottom_middle' in detected_cells:
            print("赤色が下段中央に検出されました")
            return 'bottom_middle'
        else:
            print("赤色が下段の特定の場所に検出されましたが、左右の偏りはありません")
            return 'bottom_middle'

    except Exception as e:
        print(f"カメラ撮影・グリッド処理中にエラーが発生しました: {e}")
        return 'error_in_processing'

def turn_to_relative_angle(driver, bno_sensor_wrapper_instance, angle_offset_deg, turn_speed=40, angle_tolerance_deg=3.0, max_turn_attempts=100):
    """
    現在のBNO055の方位から、指定された角度だけ相対的に旋回します。
    """
    initial_heading = bno_sensor_wrapper_instance.get_heading()
    if initial_heading is None:
        print("警告: turn_to_relative_angle: 初期方位が取得できませんでした。")
        return False
    
    target_heading = (initial_heading + angle_offset_deg + 360) % 360
    print(f"現在のBNO方位: {initial_heading:.2f}度, 相対目標角度: {angle_offset_deg:.2f}度 -> 絶対目標方位: {target_heading:.2f}度")

    loop_count = 0
    
    while loop_count < max_turn_attempts:
        current_heading = bno_sensor_wrapper_instance.get_heading()
        if current_heading is None:
            print("警告: turn_to_relative_angle: 旋回中に方位が取得できませんでした。スキップします。")
            driver.motor_stop_brake()
            time.sleep(0.1)
            loop_count += 1
            continue

        angle_error = (target_heading - current_heading + 180 + 360) % 360 - 180

        if abs(angle_error) <= angle_tolerance_deg:
            print(f"[TURN] 相対回頭完了。最終誤差: {angle_error:.2f}度 (試行回数: {loop_count})")
            driver.motor_stop_brake()
            time.sleep(0.5)
            return True

        turn_duration_on = 0.02 + (abs(angle_error) / 180.0) * 0.2
        
        if angle_error < 0:
            driver.petit_left(0, turn_speed)
            driver.petit_left(turn_speed, 0)
        else:
            driver.petit_right(0, turn_speed)
            driver.petit_right(turn_speed, 0)
            
        time.sleep(turn_duration_on)
        driver.motor_stop_brake()
        time.sleep(0.05)
        
        loop_count += 1
    
    print(f"警告: turn_to_relative_angle: 最大試行回数({max_turn_attempts}回)内に目標角度に到達できませんでした。最終誤差: {angle_error:.2f}度 (試行回数: {loop_count})")
    driver.motor_stop_brake()
    time.sleep(0.5)
    return False

# --- メイン実行ブロック ---
if __name__ == "__main__":
    # GPIO設定
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    # BNO055センサーの生インスタンス（放出判定と着地判定で直接使用）
    bno_raw_sensor = BNO055(address=0x28) 

    # --- ステージ0: 放出判定 ---
    print("\n--- ステージ0: 放出判定を開始します ---")
    is_released = check_release(
        bno_raw_sensor, # 放出判定には生インスタンスを渡す
        pressure_change_threshold=0.3,
        acc_z_threshold_abs=4.0,
        consecutive_checks=3,
        timeout=60
    )

    if is_released:
        print("\n=== ローバーの放出を確認しました！次のフェーズへ移行します。 ===")
    else:
        print("\n=== ローバーの放出は確認できませんでした。プログラムを終了します。 ===")
        # 放出失敗の場合はここで終了
        GPIO.cleanup()
        sys.exit("放出失敗")

    # 放出が確認されたら、以降のデバイスを初期化
    driver = MotorDriver(
        PWMA=12, AIN1=23, AIN2=18,
        PWMB=19, BIN1=16, BIN2=26,
        STBY=21
    )

    # pigpio初期化 (GPS用)
    pi_instance = pigpio.pi()
    if not pi_instance.connected:
        print("pigpioデーモンに接続できません。プログラムを終了します。")
        driver.cleanup()
        GPIO.cleanup()
        sys.exit()
    pi_instance.bb_serial_read_open(RX_PIN, 9600, 8)

    # BNO055Wrapperインスタンス（自律走行でget_heading()を使うため）
    bno_sensor_wrapper = BNO055Wrapper(bno_raw_sensor) 

    # カメラ初期化と設定
    picam2 = Picamera2()
    # カメラ画像を90度回転させる設定を直接Picamera2に行う
    picam2.configure(picam2.create_still_configuration(
        main={"size": (320, 240)}, # 赤色追跡用関数のサイズに合わせる
        transform=cv2.Transform(rotation=90) # libcamera.Transformの代わりにcv2.Transformを使用
    ))
    picam2.start()
    time.sleep(1) # カメラ安定待ち


    try:
        # --- ステージ1: 着地判定 ---
        print("\n--- ステージ1: 着地判定を開始します ---")
        is_landed = check_landing(
            bno_raw_sensor, # check_landing関数にはBNO055の生インスタンスを渡す
            pressure_change_threshold=0.1,
            acc_threshold_abs=0.5,
            gyro_threshold_abs=0.5,
            consecutive_checks=3,
            timeout=120, # タイムアウトを延ばす
            calibrate_bno055=True
        )

        if is_landed:
            print("\n=== ローバーの着地を確認しました！次のフェーズへ移行します。 ===")
        else:
            print("\n=== ローバーの着地は確認できませんでした。プログラムを終了します。 ===")
            raise SystemExit("着地失敗")

        # --- ステージ2: 赤色物体追跡と接近 ---
        print("\n--- ステージ2: 赤色物体を追跡し、接近します ---")
        
        print("対象物を画面内に収める")
        while True:
            percentage = get_percentage_approach(picam2) # 修正版の関数を使用
            if percentage > 5:
                print(f"赤色物体を検出しました (割合: {percentage:.2f}%)。")
                break
            else:
                print(f"赤色物体を探索中... (現在の割合: {percentage:.2f}%) 右に旋回します。")
                driver.quick_right(0, 60)
                driver.quick_right(60, 0)
                time.sleep(0.1)

        print("対象物を画面中央に収める")
        while True:
            number = get_block_number_approach(picam2) # 修正版の関数を使用
            if number == 1:
                print(f"赤色物体が左端にあります (ブロック: {number})。左に大きく旋回。")
                driver.quick_left(0, 60)
                driver.quick_left(60, 0)
            elif number == 2:
                print(f"赤色物体が左寄りにあります (ブロック: {number})。左に小さく旋回。")
                driver.quick_left(0, 45)
                driver.quick_left(45, 0)
            elif number == 3:
                print(f"赤色物体が中央にあります (ブロック: {number})。")
                break
            elif number == 4:
                print(f"赤色物体が右寄りにあります (ブロック: {number})。右に小さく旋回。")
                driver.quick_right(0, 45)
                driver.quick_right(45, 0)
            elif number == 5:
                print(f"赤色物体が右端にあります (ブロック: {number})。右に大きく旋回。")
                driver.quick_right(0, 60)
                driver.quick_right(60, 0)
            else:
                print("画面中央に調整中ですが、赤色物体が見つかりません。探索を継続します。")
                driver.quick_right(0, 60)
                driver.quick_right(60, 0)
            time.sleep(0.1)

        print("ゴール誘導を開始します")
        while True:
            percentage = get_percentage_approach(picam2) # 修正版の関数を使用
            number = get_block_number_approach(picam2)   # 修正版の関数を使用
            
            print(f"赤割合: {percentage:.2f}% ----- 画面場所:{number}です ")

            if number == 3:
                if percentage > 60:
                    print("ゴール判定。ゴール誘導を終了します")
                    driver.motor_stop_brake()
                    break
                elif percentage > 40:
                    print("赤割合が高いです (40-60%)。ゆっくり前進。")
                    driver.petit_petit(2)
                elif percentage > 20:
                    print("赤割合が中程度です (20-40%)。少し速く前進。")
                    driver.petit_petit(4)
                elif percentage > 10:
                    print("赤割合が低めです (10-20%)。さらに速く前進。")
                    driver.petit_petit(6)
                else:
                    print("赤割合が低いです (<10%)。前進して接近。")
                    following.follow_forward(driver, bno_raw_sensor, 70, 2) # bno_raw_sensorを渡す

            elif number == 1:
                print("赤色物体が左端にずれました (ブロック1)。右に大きく修正。")
                driver.petit_right(0, 100)
                driver.petit_right(100, 0)
            elif number == 2:
                print("赤色物体が左にずれました (ブロック2)。右に修正。")
                driver.petit_right(0, 90)
                driver.petit_right(90, 0)
            elif number == 4:
                print("赤色物体が右にずれました (ブロック4)。左に修正。")
                driver.petit_left(0, 90)
                driver.petit_left(90, 0)
            elif number == 5:
                print("赤色物体が右端にずれました (ブロック5)。左に大きく修正。")
                driver.petit_left(0, 100)
                driver.petit_left(100, 0)
            else:
                print("ゴール誘導中に赤色物体を見失いました。探索動作を行います。")
                driver.quick_right(0, 60)
                driver.quick_right(60, 0)
            
            time.sleep(0.1)

        driver.motor_stop_brake()
        print("赤い物体への接近段階が完了しました。")
        
        # --- ステージ3: 自律走行（GPSとカメラによる障害物回避） ---
        print("\n--- ステージ3: 自律走行（GPSとカメラによる障害物回避）を開始します ---")

        # メインの自律走行ループ
        while True:
            print("\n--- 新しい走行サイクル開始 ---")
            
            # STEP 2: GPS現在地取得し、目標方位計算
            print("\n=== ステップ2: GPS現在地取得と目標方位計算 ===")
            current_gps_coords = get_current_location(pi_instance, RX_PIN)
            goal_gps_coords = (destination_lat, destination_lon)

            if current_gps_coords[0] is None or current_gps_coords[1] is None:
                print("GPSデータが取得できませんでした。リトライします...")
                time.sleep(2)
                continue

            print(f"現在地：緯度={current_gps_coords[0]:.4f}, 経度={current_gps_coords[1]:.4f}")
            
            target_gps_heading = get_bearing_to_goal(current_gps_coords, goal_gps_coords)
            if target_gps_heading is None:
                print("警告: 目標方位の計算に失敗しました。リトライします...")
                time.sleep(2)
                continue

            print(f"GPSに基づく目標方位：{target_gps_heading:.2f}度")
            
            distance_to_goal = get_distance_to_goal(current_gps_coords, goal_gps_coords)
            print(f"目的地までの距離：{distance_to_goal:.2f}メートル")

            if distance_to_goal < 3.0: # 例: 3メートル以内になったらゴール
                print("\n🎉 目的地に到達しました！自律走行を終了します。")
                break

            # STEP 3: その場で回頭 (動的調整)
            print("\n=== ステップ3: 目標方位への回頭 (動的調整) ===")
            ANGLE_THRESHOLD_DEG = 10
            turn_speed = 90
            max_turn_attempts = 100
            turn_attempt_count = 0

            while turn_attempt_count < max_turn_attempts:
                current_bno_heading = bno_sensor_wrapper.get_heading() # ラッパーから方位取得
                if current_bno_heading is None:
                    print("警告: 旋回中にBNO055方位が取得できませんでした。リトライします。")
                    driver.motor_stop_brake()
                    time.sleep(1)
                    turn_attempt_count += 1
                    continue

                angle_error = (target_gps_heading - current_bno_heading + 180 + 360) % 360 - 180
                
                if abs(angle_error) <= ANGLE_THRESHOLD_DEG:
                    print(f"[TURN] 方位調整完了。最終誤差: {angle_error:.2f}度")
                    break

                turn_duration = 0.02 + (abs(angle_error) / 180.0) * 0.2
                if angle_error < 0:
                    print(f"[TURN] 左に回頭します (誤差: {angle_error:.2f}度, 時間: {turn_duration:.2f}秒)")
                    driver.petit_left(0, turn_speed)
                    driver.petit_left(turn_speed, 0)
                else:
                    print(f"[TURN] 右に回頭します (誤差: {angle_error:.2f}度, 時間: {turn_duration:.2f}秒)")
                    driver.petit_right(0, turn_speed)
                    driver.petit_right(turn_speed, 0)
                    
                time.sleep(turn_duration)
                driver.motor_stop_brake()
                time.sleep(0.5)

                turn_attempt_count += 1

            if turn_attempt_count >= max_turn_attempts and abs(angle_error) > ANGLE_THRESHOLD_DEG:
                print(f"警告: 最大回頭試行回数に達しましたが、目標方位に到達できませんでした。最終誤差: {angle_error:.2f}度")
            
            driver.motor_stop_brake()
            time.sleep(0.5)

            # STEP 4 & 5: カメラ検知と前進（障害物回避）
            print("\n=== ステップ4&5: カメラ検知と前進（障害物回避） ===")
            
            red_location_result = detect_red_in_grid(picam2, save_path="/home/mark1/Pictures/akairo_grid.jpg", min_red_pixel_ratio_per_cell=0.10)

            if red_location_result == 'left_bottom':
                print("赤色が左下に検出されました → 右に回頭します")
                turn_to_relative_angle(driver, bno_sensor_wrapper, 90, turn_speed=90, angle_tolerance_deg=20)
                print("回頭後、少し前進します")
                following.follow_forward(driver, bno_raw_sensor, base_speed=100, duration_time=5)
            elif red_location_result == 'right_bottom':
                print("赤色が右下に検出されました → 左に回頭します")
                turn_to_relative_angle(driver, bno_sensor_wrapper, -90, turn_speed=90, angle_tolerance_deg=20)
                print("回頭後、少し前進します")
                following.follow_forward(driver, bno_raw_sensor, base_speed=100, duration_time=5)
            elif red_location_result == 'bottom_middle':
                print("赤色が下段中央に検出されました → 右に120度回頭して前進します")
                turn_to_relative_angle(driver, bno_sensor_wrapper, 120, turn_speed=90, angle_tolerance_deg=20)
                print("120度回頭後、少し前進します (1回目)")
                following.follow_forward(driver, bno_raw_sensor, base_speed=100, duration_time=5)
                driver.motor_stop_brake()
                time.sleep(0.5)

                print("さらに左に30度回頭し、前進します。")
                turn_to_relative_angle(driver, bno_sensor_wrapper, -30, turn_speed=90, angle_tolerance_deg=20)
                print("左30度回頭後、少し前進します (2回目)")
                following.follow_forward(driver, bno_raw_sensor, base_speed=100, duration_time=5)
            elif red_location_result == 'high_percentage_overall':
                print("画像全体に高割合で赤色を検出 → パラシュートが覆いかぶさっている可能性。長く待機して様子を見ます")
                time.sleep(10)
                print("待機後、少し前進します")
                following.follow_forward(driver, bno_raw_sensor, base_speed=90, duration_time=3)
            elif red_location_result == 'none_detected':
                print("赤色を検出しませんでした → 方向追従制御で前進します。(速度80, 5秒)")
                following.follow_forward(driver, bno_raw_sensor, base_speed=90, duration_time=5)
            elif red_location_result == 'error_in_processing':
                print("カメラ処理でエラーが発生しました。少し待機します...")
                time.sleep(2)

            driver.motor_stop_brake()

            # 回避後の再確認ロジック（3点スキャン）
            print("\n=== 回避後の周囲確認を開始します (3点スキャン) ===")
            
            # 1. ローバーを目的地のGPS方向へ再度向ける
            print("\n=== 回避後: 再度目的地の方位へ回頭 ===")
            turn_speed_realign = 80
            angle_tolerance_realign = 10
            max_turn_attempts_realign = 100
            turn_attempt_count_realign = 0

            while turn_attempt_count_realign < max_turn_attempts_realign:
                current_bno_heading = bno_sensor_wrapper.get_heading()
                if current_bno_heading is None:
                    print("警告: 再調整中にBNO055方位が取得できませんでした。リトライします。")
                    driver.motor_stop_brake()
                    time.sleep(1)
                    turn_attempt_count_realign += 1
                    continue

                angle_error = (target_gps_heading - current_bno_heading + 180 + 360) % 360 - 180
                
                if abs(angle_error) <= angle_tolerance_realign:
                    print(f"[RE-ALIGN] GPS方向への再調整完了。最終誤差: {angle_error:.2f}度")
                    break

                turn_duration = 0.02 + (abs(angle_error) / 180.0) * 0.2
                if angle_error < 0:
                    print(f"[RE-ALIGN] 左に回頭します (誤差: {angle_error:.2f}度, 時間: {turn_duration:.2f}秒)")
                    driver.petit_left(0, turn_speed_realign)
                    driver.petit_left(turn_speed_realign, 0)
                else:
                    print(f"[RE-ALIGN] 右に回頭します (誤差: {angle_error:.2f}度, 時間: {turn_duration:.2f}秒)")
                    driver.petit_right(0, turn_speed_realign)
                    driver.petit_right(turn_speed_realign, 0)
                    
                time.sleep(turn_duration)
                driver.motor_stop_brake()
                time.sleep(0.05)
                turn_attempt_count_realign += 1
                
            if turn_attempt_count_realign >= max_turn_attempts_realign and abs(angle_error) > angle_tolerance_realign:
                print(f"警告: 回避後の目的地方位への回頭が不十分です。最終誤差: {angle_error:.2f}度")
            driver.motor_stop_brake()
            time.sleep(0.5)

            # 2. 正面、左30度、右30度の3方向で赤色検知
            scan_results = {
                'front': 'none_detected',
                'left_30': 'none_detected',
                'right_30': 'none_detected'
            }
            
            print("→ 正面方向の赤色を確認します...")
            scan_results['front'] = detect_red_in_grid(picam2, save_path="/home/mark1/Pictures/confirm_front.jpg", min_red_pixel_ratio_per_cell=0.10)

            print("→ 左に30度回頭し、赤色を確認します...")
            turn_to_relative_angle(driver, bno_sensor_wrapper, -30, turn_speed=90, angle_tolerance_deg=10)
            scan_results['left_30'] = detect_red_in_grid(picam2, save_path="/home/mark1/Pictures/confirm_left.jpg", min_red_pixel_ratio_per_cell=0.10)
            print("→ 左30度から正面に戻します...")
            turn_to_relative_angle(driver, bno_sensor_wrapper, 30, turn_speed=90, angle_tolerance_deg=10)

            print("→ 右に30度回頭し、赤色を確認します...")
            turn_to_relative_angle(driver, bno_sensor_wrapper, 30, turn_speed=90, angle_tolerance_deg=10)
            scan_results['right_30'] = detect_red_in_grid(picam2, save_path="/home/mark1/Pictures/confirm_right.jpg", min_red_pixel_ratio_per_cell=0.10)
            print("→ 右30度から正面に戻します...")
            turn_to_relative_angle(driver, bno_sensor_wrapper, -30, turn_speed=90, angle_tolerance_deg=10)

            is_front_clear = (scan_results['front'] == 'none_detected')
            is_left_clear = (scan_results['left_30'] == 'none_detected')
            is_right_clear = (scan_results['right_30'] == 'none_detected')

            if is_front_clear and is_left_clear and is_right_clear:
                print("\n=== 3点スキャン結果: 全ての方向でパラシュートは検出されませんでした。回避成功、ミッション継続！ ===")
            else:
                print("\n=== 3点スキャン結果: まだパラシュートが検出されました。再回避を試みます。 ===")
                print(f"検出詳細: 正面: {scan_results['front']}, 左30: {scan_results['left_30']}, 右30: {scan_results['right_30']}")
                
                if scan_results['left_30'] != 'none_detected':
                    print("左30度で検出されたため、右90度回頭して回避します。")
                    turn_to_relative_angle(driver, bno_sensor_wrapper, 90, turn_speed=90, angle_tolerance_deg=20)
                elif scan_results['right_30'] != 'none_detected':
                    print("右30度で検出されたため、左90度回頭して回避します。")
                    turn_to_relative_angle(driver, bno_sensor_wrapper, -90, turn_speed=90, angle_tolerance_deg=20)
                elif scan_results['front'] != 'none_detected':
                    print("正面で検出されたため、右120度回頭して回避します。")
                    turn_to_relative_angle(driver, bno_sensor_wrapper, 120, turn_speed=90, angle_tolerance_deg=20)
                    driver.motor_stop_brake()
                    time.sleep(0.5)

                    print("さらに左に30度回頭し、前進します。")
                    turn_to_relative_angle(driver, bno_sensor_wrapper, -30, turn_speed=90, angle_tolerance_deg=20)
                    print("左30度回頭後、少し前進します (2回目)")
                    following.follow_forward(driver, bno_raw_sensor, base_speed=100, duration_time=5)
                else:
                    print("詳細不明な検出のため、右120度回頭して回避します。")
                    turn_to_relative_angle(driver, bno_sensor_wrapper, 120, turn_speed=90, angle_tolerance_deg=20.0)
                
                following.follow_forward(driver, bno_raw_sensor, base_speed=90, duration_time=5)
                driver.motor_stop_brake()
                time.sleep(1)
                
                # メインループの先頭に戻り、GPS取得から再開

    except SystemExit as e:
        print(f"\nプログラムが強制終了されました: {e}")
    except Exception as e:
        print(f"\nメイン処理中に予期せぬエラーが発生しました: {e}")
    finally:
        if 'driver' in locals():
            driver.cleanup()
        if 'pi_instance' in locals() and pi_instance.connected:
            pi_instance.bb_serial_read_close(RX_PIN)
            pi_instance.stop()
        if 'picam2' in locals():
            picam2.close()
        # BNO055のクリーンアップは不要 (smbusが自動的に管理)
        GPIO.cleanup()
        print("=== すべてのクリーンアップが終了しました。プログラムを終了します。 ===")
