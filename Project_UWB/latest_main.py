 
"""
LD2460 + MQTT Publisher (WiFiManager + Captive Portal)
-------------------------------------------------------
- เปิด AP 'LD2460_Config' พร้อม DNS redirect (Captive Portal)
- เมื่อเชื่อมต่อ WiFi แล้ว browser จะเปิดหน้า config อัตโนมัติ
- กรอก: SSID, Password, MQTT Server, Username, Password, Client ID
- Topic auto = uwb/[Client_ID]/count/
- ส่ง MQTT เฉพาะตอนจำนวนเปลี่ยนแปลง
"""

import network, socket, time, gc, ujson, machine, _thread, ure, os
from umqtt.simple import MQTTClient
from machine import UART
from ld2460_driver import LD2460

CONFIG_FILE = "config.json"
MQTT_PORT = 1883
NODE_ID = "uwb20250001" 

# -------------------------------------------------------------
# Config management
# -------------------------------------------------------------
def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return ujson.load(f)
    except:
        return {}
    
def delete_config():
    try:
        os.remove(CONFIG_FILE)
        print("Config deleted.")
    except:
        print("No config to delete.")

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        ujson.dump(data, f)

# -------------------------------------------------------------
# Captive Portal DNS Redirect
# -------------------------------------------------------------
def start_dns_redirect(ap_ip="192.168.4.2"):
    def dns_thread():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("", 53))
        print("DNS redirect active (Captive Portal)...")
        while True:
            try:
                data, addr = s.recvfrom(512)
                if data:
                    # respond with A record → redirect to AP IP
                    response = data[:2] + b"\x81\x80" + data[4:6] + data[4:6] + b"\x00\x00\x00\x00" + data[12:]
                    response += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + bytes(map(int, ap_ip.split(".")))
                    s.sendto(response, addr)
            except:
                pass
    _thread.start_new_thread(dns_thread, ())

# -------------------------------------------------------------
# WiFiManager Portal (with captive redirect)
# -------------------------------------------------------------
    
def start_config_portal():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="UWB_NODE01", password="12345678")
    ip = ap.ifconfig()[0]
    print("\nWiFi Config Portal started!")
    print("Connect to SSID: LD2460_Config (pass: 12345678)")
    print("Then open any web browser — auto redirect to setup page.")

    start_dns_redirect(ip)  # start DNS redirect thread

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)

    html = """<!DOCTYPE html><html><head><meta charset='utf-8'>
    <title>LD2460 Setup</title>
    <style>
    body{font-family:sans-serif;background:#f8f8f8;padding:20px;}
    h2{color:#333;}
    input{width:90%%;padding:6px;margin:4px 0;}
    input[type=submit]{background:#007bff;color:white;border:none;padding:8px 16px;border-radius:6px;}
    form{background:white;padding:20px;border-radius:12px;box-shadow:0 0 10px #ccc;max-width:400px;}
    </style></head><body>
    <h2>LD2460 WiFi + MQTT Config</h2>
    <form action='/' method='post'>
      <b>WiFi Settings</b><br>
      SSID:<br><input name='ssid'><br>
      Password:<br><input name='password'><br><br>
      <b>MQTT Settings</b><br>
      Broker IP / Hostname:<br><input name='mqtt_server'><br>
      MQTT Username:<br><input name='mqtt_user'><br>
      MQTT Password:<br><input name='mqtt_pass'><br>
      NODE ID:<br><input name='mqtt_client_id'><br><br>
      <input type='submit' value='Send'>
    </form></body></html>"""

    while True:
        cl, addr = s.accept()
        try:
            # อ่าน request เบื้องต้น
            req = cl.recv(2048)
            if not req:
                cl.close()
                continue
            try:
                req_text = req.decode()
            except:
                req_text = str(req)

            # ตรวจสอบว่าเป็น POST หรือไม่
            if "POST" in req_text.split("\r\n", 1)[0]:
                # หา header lines เพื่อดึง Content-Length (ถ้ามี)
                headers_part = ""
                body = ""
                if "\r\n\r\n" in req_text:
                    headers_part, body = req_text.split("\r\n\r\n", 1)
                else:
                    headers_part = req_text

                content_length = None
                for line in headers_part.split("\r\n"):
                    low = line.lower()
                    if low.startswith("content-length:"):
                        try:
                            content_length = int(line.split(":", 1)[1].strip())
                        except:
                            content_length = None
                        break

                # ถ้ามี Content-Length ให้แน่ใจว่าอ่านครบตามความยาว
                if content_length is not None:
                    body_bytes = body.encode() if isinstance(body, str) else body
                    remaining = content_length - len(body_bytes)
                    while remaining > 0:
                        more = cl.recv(remaining)
                        if not more:
                            break
                        body_bytes += more
                        remaining -= len(more)
                    try:
                        body = body_bytes.decode()
                    except:
                        body = ""
                else:
                    # ถ้าไม่มี header ให้พยายามอ่านที่เหลือแบบสั้นๆ (fallback)
                    # ถ้า browser ส่งครบในครั้งแรกก็ใช้ body ที่แยกได้
                    pass

                # now parse body (x-www-form-urlencoded)
                params = {}
                for pair in body.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        # URL decode: + -> space, %xx -> char
                        v = ure.sub(r'\+', ' ', v)
                        v = ure.sub(r'%([0-9A-Fa-f]{2})', lambda m: chr(int(m.group(1), 16)), v)
                        params[k] = v

                # ถ้า form ไม่ส่งบาง field ให้เซ็ต default เป็น empty string
                config_data = {
                    "ssid": params.get("ssid", ""),
                    "password": params.get("password", ""),
                    "mqtt_server": params.get("mqtt_server", ""),
                    "mqtt_user": params.get("mqtt_user", ""),
                    "mqtt_pass": params.get("mqtt_pass", ""),
                    "mqtt_client_id": params.get("mqtt_client_id", ""),
                }

                # ตรวจสอบ client_id ถ้าว่าง ให้ใช้ default "1"
                client_id = config_data.get("mqtt_client_id") or "1"
                config_data["mqtt_topic"] = f"uwb/{client_id}/count/"

                print("Saving config:", config_data)
                # บันทึกเป็น JSON (เรียกฟังก์ชัน save_config ของคุณ)
                save_config(config_data)
                # อ่านกลับมาโชว์ (debug)
                try:
                    with open(CONFIG_FILE, "r") as f:
                        print("Saved file content:", f.read())
                except Exception as e:
                    print("Read saved file error:", e)

                # ตอบกลับและรีบูต
                cl.send("HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
                        "<html><body><h3>✅ Config saved. Rebooting...</h3></body></html>")
                cl.close()
                time.sleep(2)
                machine.reset()
            else:
                # GET หรือ อื่นๆ → ส่งหน้า HTML
                cl.send("HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n" + html)
                cl.close()
        except Exception as e:
            print("Config portal error:", e)
            try:
                cl.close()
            except:
                pass
# -------------------------------------------------------------
def thread_portal():
    try:
        start_config_portal()
    except Exception as e:
        print("Portal thread stopped:", e)
        
# WiFi connect
# -------------------------------------------------------------
def connect_wifi(ssid, password):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print(f"Connecting to {ssid} ...")
        wlan.connect(ssid, password)
        timeout = 30
        while not wlan.isconnected() and timeout > 0:
            print(".", end="")
            time.sleep(1)
            timeout -= 1
        print()
    if wlan.isconnected():
        print("WiFi connected, IP:", wlan.ifconfig()[0])
        return True
    else:
        print("WiFi connection failed")
        return False

# -------------------------------------------------------------
# MQTT connect / publish
# -------------------------------------------------------------
def connect_mqtt(config):
    try:
        client = MQTTClient(
            client_id=config["mqtt_client_id"],
            server=config["mqtt_server"],
            port=MQTT_PORT,
            user=config.get("mqtt_user", None),
            password=config.get("mqtt_pass", None)
        )
        client.connect()
        print("MQTT connected to", config["mqtt_server"])
        return client
    except Exception as e:
        print("MQTT connect error:", e)
        return None

def safe_publish(client, topic, message):
    try:
        client.publish(topic, message)
        return True
    except Exception as e:
        print("Publish failed:", e)
        return False

# -------------------------------------------------------------
# MAIN
# -------------------------------------------------------------
if __name__ == "__main__":
    if CONFIG_FILE in os.listdir():
        print("Found old config → deleting...")
        delete_config()
    else:
        print("No existing config found.")
    # --------------------
    
    try:
        sensor = LD2460(uart_id=2, tx_pin=17, rx_pin=16, baudrate=115200)
        sensor.calibrate()
        sensor.set_install_mode(LD2460.MODE_SIDE_MOUNT)
        sensor.set_detection_range(4, 45, 135)
    except Exception as e:
        print("LD2460 init failed:", e)
        machine.reset()
        
    _thread.start_new_thread(thread_portal, ())
    last_count = -1

    # ======== Loop หลัก =========
    while True:
        try:
            # อ่าน sensor ตลอดเวลา
            if sensor.read_data():
                count = len(sensor.get_targets())
                if count != last_count:
                    print(f"[Sensor] Count: {count}")
                    last_count = count

            config = load_config()
            # ถ้ายังไม่มี config → เปิด config portal
            if config and config.get("mqtt_client_id"):
                client_id = config["mqtt_client_id"]

                if client_id == NODE_ID:
                    print(f"\n[MQTT] Client ID match ({client_id}) → Publishing...")

                    # เชื่อมต่อ WiFi
                    if connect_wifi(config["ssid"], config["password"]):
                        client = connect_mqtt(config)
                        if client:
                            topic = f"uwb/{client_id}/count/"
                            msg = ujson.dumps({"count": count})
                            if safe_publish(client, topic, msg):
                                print(f"Published → {topic}: {msg}")
                            else:
                                print("Publish failed.")
                            client.disconnect()

                    # ลบ config หลังส่งสำเร็จ เพื่อรอ config ใหม่
                    delete_config()
                    print("Config deleted — waiting for next setup...")

            time.sleep(0.5)
            gc.collect()

        except Exception as e:
            print("Main loop error:", e)
            time.sleep(1)
