"""
LD2460 + MQTT Publisher (Auto Broker Discovery via mDNS)
-------------------------------------------------------
- อ่านจำนวน target จาก LD2460
- ค้นหา Broker อัตโนมัติผ่าน mDNS (mqtt-broker.local)
- หาก mDNS ใช้ไม่ได้ → fallback ไปใช้ IP เดิม
- ส่ง MQTT เฉพาะตอนจำนวนเปลี่ยนแปลง
- พร้อมระบบ reconnect และ garbage management
"""

import network
import time
import gc
from umqtt.simple import MQTTClient
from machine import UART
import ujson
import socket

# ----------------------------
# WiFi settings
SSID = "Papon"
PASSWORD = "0649624003"

# Broker hostname (ประกาศจาก ESP32-S3)
BROKER_HOSTNAME = "mqtt-brokers3.local"
BROKER_FALLBACK_IP = "10.230.65.154"
MQTT_PORT = 1883
MQTT_CLIENT_ID = "ld2460_1"
MQTT_TOPIC_DETECT = b"sensor/detection/1"

# ----------------------------
# Import LD2460 driver
from ld2460_driver import LD2460


# ----------------------------
# WiFi connect
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to WiFi...")
        wlan.connect(SSID, PASSWORD)
        timeout = 60
        while not wlan.isconnected() and timeout > 0:
            print(".", end="")
            time.sleep(1)
            timeout -= 1

    if wlan.isconnected():
        print("\nWiFi connected, IP:", wlan.ifconfig()[0])
        return True
    else:
        print("\nWiFi connection failed")
        return False


# ----------------------------
# mDNS resolver
def resolve_mdns(hostname):
    try:
        addr_info = socket.getaddrinfo(hostname, MQTT_PORT)[0][-1][0]
        print("Found broker via mDNS:", addr_info)
        return addr_info
    except Exception as e:
        print("mDNS lookup failed:", e)
        return None


# ----------------------------
# Connect MQTT
def connect_mqtt(broker_ip):
    try:
        client = MQTTClient(MQTT_CLIENT_ID, broker_ip, port=MQTT_PORT)
        client.connect()
        print("Connected to MQTT broker:", broker_ip)
        return client
    except Exception as e:
        print("MQTT connection failed:", e)
        return None


# ----------------------------
# Safe MQTT publish
def safe_publish(client, topic, message):
    try:
        client.publish(topic, message)
        return True
    except Exception as e:
        print("MQTT publish failed:", e)
        return False


# ----------------------------
# MAIN PROGRAM
if __name__ == "__main__":
    print("\nLD2460 + MQTT Publisher starting...")
    print(f"Free memory: {gc.mem_free()} bytes")

    # Connect WiFi
    if not connect_wifi():
        raise SystemExit

    # Discover broker via mDNS
    broker_ip = resolve_mdns(BROKER_HOSTNAME) or BROKER_FALLBACK_IP
    print(f"Using MQTT Broker: {broker_ip}")

    # Connect MQTT
    client = connect_mqtt(broker_ip)
    if not client:
        print("Running without MQTT (offline mode)")

    # Initialize LD2460 (UART2: TX=17, RX=16)
    try:
        sensor = LD2460(uart_id=2, tx_pin=17, rx_pin=16, baudrate=115200)
        sensor.calibrate()
        sensor.set_install_mode(LD2460.MODE_SIDE_MOUNT) # and MODE_SIDE_MOUNT
        sensor.set_detection_range(2.5, 45 , 135)  # ( meter , start angle , end angel ) 
    except Exception as e:
        print("LD2460 init failed:", e)
        raise SystemExit

    last_count = -1
    error_count = 0
    max_errors = 10
    last_gc_time = time.time()
    last_mqtt_check = time.time()
    mqtt_reconnect_attempts = 0
    max_mqtt_reconnect = 2

    while True:
        try:
            # Garbage collection every 30s
            if time.time() - last_gc_time > 30:
                gc.collect()
                print(f"🧹 GC: Free memory: {gc.mem_free()} bytes")
                last_gc_time = time.time()

            # Check MQTT connection every 60s
            if client and time.time() - last_mqtt_check > 60:
                last_mqtt_check = time.time()
                try:
                    client.ping()
                    print(" MQTT alive")
                    mqtt_reconnect_attempts = 0
                except:
                    print(" MQTT lost, reconnecting...")
                    broker_ip = resolve_mdns(BROKER_HOSTNAME) or BROKER_FALLBACK_IP
                    client = connect_mqtt(broker_ip)
                    mqtt_reconnect_attempts += 1
                    if mqtt_reconnect_attempts > max_mqtt_reconnect:
                        print("Rebooting due to MQTT failure...")
                        import machine
                        machine.reset()

            # Read LD2460 data
            if sensor.read_data():
                targets = sensor.get_targets()
                count = len(targets)

                if count != last_count:
                    payload = {"people_count": count}
                    msg = ujson.dumps(payload)
                    if client:
                        if safe_publish(client, MQTT_TOPIC_DETECT, msg):
                            print(f"Published: {count} person(s)")
                        else:
                            client = connect_mqtt(broker_ip)
                    else:
                        print(f"Detected: {count} person(s) (MQTT offline)")

                    last_count = count
                    error_count = 0

            time.sleep(0.3)

        except MemoryError:
            print(" Memory error, clearing buffers...")
            sensor.clear_buffer()
            gc.collect()
            print(f"Free memory after GC: {gc.mem_free()} bytes")
            time.sleep(1)

        except Exception as e:
            error_count += 1
            print(f"Error ({error_count}/{max_errors}): {e}")
            if error_count >= max_errors:
                print("Restarting sensor due to errors...")
                try:
                    sensor.restart()
                    error_count = 0
                    print("Sensor restarted")
                except:
                    print("Rebooting system...")
                    import machine
                    machine.reset()
            time.sleep(1)
