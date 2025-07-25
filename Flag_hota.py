import time
import smbus
import struct
# ここに following.py をインポートします
import following # 新しく追加

import cv2
import math
import numpy as np
from picamera2 import Picamera2
from BNO055 import BNO055
from motor import MotorDriver
from Flag_Detector3 import FlagDetector # Flag_Detector3 を使用していることを確認
import RPi.GPIO as GPIO

# --- 設定値 ---
TARGET_SHAPES = ["三角形", "長方形", "T字", "十字"]
AREA_THRESHOLD_PERCENT = 20.0

def find_target_flag(detected_data, target_name):
    """検出データから指定された図形(target_name)のフラッグを探して返す"""
    for flag in detected_data:
        for shape in flag['shapes']:
            if shape['name'] == target_name:
                return flag
    return None

# メインの実行ブロック
if __name__ == '__main__':
    # --- 初期化処理 ---
    detector = FlagDetector()
    driver = MotorDriver(
        PWMA=12, AIN1=23, AIN2=18,    # 左モーター
        PWMB=19, BIN1=16, BIN2=26,    # 右モーター
        STBY=21
    )
    screen_area = detector.width * detector.height

    # === BNO055 初期化 ===
    bno = BNO055()
    if not bno.begin():
        print("BNO055の初期化に失敗しました。")
        exit(1)
    time.sleep(1)
    bno.setExternalCrystalUse(True)
    bno.setMode(BNO055.OPERATION_MODE_NDOF)
    time.sleep(1)
    print("センサー類の初期化完了。")

    try:
        # --- 全てのターゲットに対してループ ---
        for target_name in TARGET_SHAPES:
            print(f"\n---====== 新しい目標: [{target_name}] の探索を開始します ======---")

            task_completed = False
            while not task_completed:

                # --- 探索 ---
                print(f"[{target_name}] を探しています...")
                detected_data = detector.detect()
                target_flag = find_target_flag(detected_data, target_name)

                # 見つからない場合は回転して探索
                if target_flag is None:
                    print(f"[{target_name}] が見つかりません。回転して探索します。")
                    search_count = 0
                    while target_flag is None and search_count < 50: # タイムアウト設定
                        driver.petit_right(0, 70)
                        driver.petit_right(70, 0)
                        driver.motor_stop_brake()
                        time.sleep(2.0)

                        detected_data = detector.detect()
                        target_flag = find_target_flag(detected_data, target_name)
                        search_count += 1

                    if target_flag is None:
                        print(f"探索しましたが [{target_name}] は見つかりませんでした。次の目標に移ります。")
                        break

                # --- 図形が見つかった場合（ここを修正） ---
                if target_flag is not None:
                    print(f"[{target_name}] を発見！IMU制御で前進します。")
                    # following.py の follow_petit_forward を呼び出す
                    # 第1引数: driver (MotorDriverインスタンス)
                    # 第2引数: bno (BNO055インスタンス)
                    # 第3引数: base_speed (基本速度、例: 30)
                    # 第4引数: duration_time (前進させる時間、例: 0.5秒)
                    following.follow_petit_forward(driver, bno, 30, 0.5) # 速度30で0.5秒前進する例

                    driver.motor_stop_brake() # 動作後に必ず停止

                    # 前進後に再度図形を探す
                    print("前進後、再度図形を探索します...")
                    detected_data = detector.detect()
                    target_flag = find_target_flag(detected_data, target_name)

                    if target_flag is None:
                        print(f"前進中に [{target_name}] を見失いました。再探索を開始します。")
                        continue

                # --- 追跡（中央寄せ＆接近）---
                print(f"[{target_name}] を発見！追跡を開始します。")
                while target_flag:
                    # --- 中央寄せ ---
                    if target_flag['location'] != '中央':
                        print(f"位置を調整中... (現在位置: {target_flag['location']})")
                        if target_flag['location'] == '左':
                            driver.petit_right(0, 60)
                            driver.petit_right(60, 0)
                            driver.motor_stop_brake()
                            time.sleep(1.0)
                        elif target_flag['location'] == '右':
                            driver.petit_left(0, 60)
                            driver.petit_left(60, 0)
                            driver.motor_stop_brake()
                            time.sleep(1.0)

                        print("  再検出中...")
                        detected_data = detector.detect()
                        target_flag = find_target_flag(detected_data, target_name)

                        if not target_flag:
                            print(f"調整中に [{target_name}] を見失いました。")
                            break

                        continue

                    # --- 接近 ---
                    else:
                        flag_area = cv2.contourArea(target_flag['flag_contour'])
                        screen_area_calc = detector.width * detector.height
                        area_percent = (flag_area / screen_area_calc) * 100
                        print(f"中央に補足。接近中... (画面占有率: {area_percent:.1f}%)")

                        if area_percent >= AREA_THRESHOLD_PERCENT:
                            print(f"[{target_name}] に接近完了！")
                            task_completed = True
                            time.sleep(1)
                            break
                        else:
                            driver.petit_petit(2)

                    print("  再検出中...")
                    detected_data = detector.detect()
                    target_flag = find_target_flag(detected_data, target_name)

                    if not target_flag:
                        print(f"追跡中に [{target_name}] を見失いました。再探索します。")
                        break

        print("\n---====== 全ての目標の探索が完了しました ======---")

    finally:
        # --- 終了処理 ---
        print("--- 制御を終了します ---")
        driver.cleanup()
        detector.close()
        GPIO.cleanup()
        cv2.destroyAllWindows()
