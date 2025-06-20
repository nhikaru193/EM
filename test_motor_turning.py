import time
import math
import RPi.GPIO as GPIO
from motor import MotorDriver  # ユーザーのMotorDriverクラスを使用
from BNO055 import BNO055
import smbus
import struct
import serial
import pynmea2

def measurement_turning(a, b):
    dturn = b - a
    return dturn

def test_turning(duty):
    print(f"デューティ比{duty}まで加速中です..")
    driver.changing_right(0, duty)
    print(f"デューティ比{duty}まで加速完了 + 角変化量測定を開始します")
    Departure_point = bno.get_heading()
    time.sleep(1.2)
    print("角変化量計測終了 + 減速を開始します")
    Arrival_point = bno.get_heading()
    driver.changing_right(duty, 0)
    dturn = measurement_turning(Departure_point, Arrival_point)
    average = dturn / 1.2
    print(f"計測終了です。デューティ比{duty}において")
    print(f"総回転角度は{dturn} 度 です")
    print(f"平均角速度は{average} 度/s です")
    time.sleep(2)

#モータの初期化
driver = MotorDriver(
    PWMA=12, AIN1=23, AIN2=18,   # 左モーター用（モータA）
    PWMB=19, BIN1=16, BIN2=26,   # 右モーター用（モータB）
    STBY=21                      # STBYピン
)

#BNO055のインスタンス作成
bno = BNO055()
if bno.begin() is not True:
        print("Error initializing device")
        exit()
    time.sleep(1)
bno.setMode(BNO055.OPERATION_MODE_NDOF)
bno.setExternalCrystalUse(True)
time.sleep(1)
for i in range (9):
  i = i + 1
  test_turning(10 * i)
  
