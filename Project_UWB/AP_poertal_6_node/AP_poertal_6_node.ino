#include <WiFi.h>
#include <PicoMQTT.h>

// ==================== WiFi Config ====================
const char* ssid = "Papon";
const char* password = "0649624003";

// ==================== Custom MQTT Server ====================
class MyMQTTServer : public PicoMQTT::Server {
  void on_message(const char* topic, PicoMQTT::IncomingPacket& packet) override {
    String payload = "";
    while (packet.available()) payload += (char)packet.read();
    Serial.printf("[MQTT] Topic: %s | Payload: %s\n", topic, payload.c_str());
  }
};

// สร้าง instance
MyMQTTServer mqtt;

// ==================== Setup ==========================
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== ESP32-S3 MQTT Broker (Subscriber Only) ===");

  // ---- เชื่อมต่อ WiFi ----
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.printf("Connecting to %s", ssid);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected! IP: %s\n", WiFi.localIP().toString().c_str());

  // ---- เริ่มต้น MQTT Broker (port 1883 by default) ----
  mqtt.begin();
  Serial.println("MQTT Broker started on port 1883!");
  Serial.printf("Broker running at: mqtt://%s:1883\n", WiFi.localIP().toString().c_str());

  // ---- Subscribe รับข้อมูลจากทั้ง 6 โหนด ----
  mqtt.subscribe("uwb/uwb20250001/count/");
  mqtt.subscribe("uwb/uwb20250002/count/");
  mqtt.subscribe("uwb/uwb20250003/count/");
  mqtt.subscribe("uwb/uwb20250004/count/");
  mqtt.subscribe("uwb/uwb20250005/count/");
  mqtt.subscribe("uwb/uwb20250006/count/");

  Serial.println("Subscribed to all 6 UWB node topics.");
}

// ==================== Loop ==========================
void loop() {
  mqtt.loop();  // ให้ Broker ทำงานต่อเนื่อง
}
