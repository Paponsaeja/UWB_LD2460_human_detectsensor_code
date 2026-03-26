#include <WiFi.h>
#include <PicoMQTT.h> // Local Broker
#include <PubSubClient.h> // Cloud Client
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <freertos/queue.h>
#include <WiFiUdp.h>
#include <WiFiManager.h>
#include <Preferences.h>

// Cloud MQTT Settings
const char* CLOUD_MQTT_SERVER = "broker.hivemq.com"; // ตัวอย่าง Cloud Broker
const int CLOUD_MQTT_PORT = 1883; // หรือ 8883 สำหรับ SSL
const char* CLOUD_MQTT_CLIENT_ID = "LD2460_Gateway_Broker";
const char* CLOUD_MQTT_TOPIC = "sensor/cloud/totalSet";
/*
// Global Variables สำหรับเก็บค่า Custom Parameters
char mqtt_user[20] = "";
char mqtt_pass[20] = "";

// Preferences Object
Preferences preferences;

// ... (Function prototypes)
void loadCustomParams(); // ฟังก์ชันสำหรับโหลดค่าจาก NVS
void saveCustomParams(); // ฟังก์ชันสำหรับบันทึกค่าลง NVS
*/
// InfluxDB Cloud Settings
const char* influx_url = "https://us-east-1-1.aws.cloud2.influxdata.com/api/v2/write";
const char* influx_org = "LD2460";
const char* influx_bucket = "LD2460_sensor";
const char* influx_token = "A4v2YaIWU31Bsb3iz8i39E3qg75GrrLCXHOHqGZL5N7U148KcOj22i4y7lS27BPVajZUiHoIP3BkAAM07_T-Cw==";

const char* measurement_name = "occupancy";
const char* device_tag = "LD2460_Sensor";

// MQTT Broker (Local)
const int mqtt_port = 1883;
PicoMQTT::Server mqtt;

// Cloud Client Objects
WiFiClient espClient;
PubSubClient cloudClient(espClient);

// Node storage
int node1 = 0, node2 = 0;
int node3 = 0, node4 = 0;
int node5 = 0, node6 = 0;

// Data aggregation
long runningSum = 0;
int runningCount = 0;
int avg_old = -1;
unsigned long startTime = 0;
const unsigned long ONE_MINUTE = 60000;
int currentValue = -1;
unsigned long currentDuration = 0;
int longestValue = -1;
unsigned long longestDuration = 0;
int value;
static unsigned long lastUpdateTime = 0;
static unsigned long valueDuration = 0;
static bool isFirstValue = true;
static int lastTotalSet = -1;

// UDP Discovery
WiFiUDP udp;
const int DISCOVERY_PORT = 4210;
const char* DISCOVERY_MSG = "DISCOVER_BROKER";
const char* RESPONSE_MSG = "BROKER_HERE";

// Queue for totalSet
QueueHandle_t dataQueue;

// Function prototypes
void mqttTask(void *pvParameters);
void dataTask(void *pvParameters);
void wifiMemoryTask(void *pvParameters);
void cloudMqttTask(void *pvParameters); // ** New Task for Cloud Client **
void processMinuteData();
void sendToInfluxDB(int average_count);
void connectWiFi();
void reconnectCloudMQTT();

void setup() {
    Serial.begin(115200);
    delay(1000);

    connectWiFi(); // ใช้ WiFiManager
    
    // ตั้งค่า Cloud MQTT Client
    cloudClient.setServer(CLOUD_MQTT_SERVER, CLOUD_MQTT_PORT);

    startTime = millis();

    // Create queue
    dataQueue = xQueueCreate(50, sizeof(int));
    if(!dataQueue) while(1);

    // Subscribe topic (รับข้อมูลจาก LD2460 Client ภายใน LAN)
    mqtt.subscribe("sensor/detection/#", [](const char* topic, const char* payload) {
        Serial.printf("\n--- New MQTT Message ---\nTopic: %s\nPayload: %s\n", topic, payload);
        StaticJsonDocument<128> doc;
        if(deserializeJson(doc, payload)) return;

        int people = doc["people_count"];
        String t = String(topic);

        if(t.endsWith("/1")) node1 = people;
        else if(t.endsWith("/2")) node2 = people;
        else if(t.endsWith("/3")) node3 = people;
        else if(t.endsWith("/4")) node4 = people;
        else if(t.endsWith("/5")) node5 = people;
        else if(t.endsWith("/6")) node6 = people;

        int m12 = (node1 == node2) ? node1 : 0;
        int m34 = (node3 == node4) ? node3 : 0;
        int m56 = (node5 == node6) ? node5 : 0;
        int totalSet = m12 + m34 + m56;
        Serial.printf(" Pair(1,2)=%d, Pair(3,4)=%d, Pair(5,6)=%d → Total Set=%d\n",
                      m12, m34, m56, totalSet);

        // publish totalSet ภายใน Local Broker (ให้ Client อื่นๆ ภายใน LAN)
        if(totalSet != lastTotalSet){
            char totalStr[2];
            sprintf(totalStr, "%d", totalSet);
            mqtt.publish("sensor/totalSet", totalStr);
            
            // ** Publish ไปยัง Cloud Broker ด้วย **
            if (cloudClient.connected()){
                cloudClient.publish(CLOUD_MQTT_TOPIC, totalStr);
                Serial.println("→ Published totalSet to Cloud.");
            }
            
            lastTotalSet = totalSet;
        }
        xQueueSendToBack(dataQueue, &totalSet, 10 / portTICK_PERIOD_MS);
        doc.clear();
    });

    mqtt.begin();
    
    // Create tasks
    xTaskCreate(mqttTask, "Local MQTT Task", 4096, NULL, 2, NULL);
    xTaskCreate(dataTask, "Data Task", 4096, NULL, 1, NULL);
    xTaskCreate(wifiMemoryTask, "WiFi/Memory Task", 4096, NULL, 1, NULL);
    xTaskCreate(cloudMqttTask, "Cloud MQTT Task", 4096, NULL, 1, NULL); // ** New Task **
}

void loop() {
    int packetSize = udp.parsePacket();
    if(packetSize){
        char incoming[20];
        int len = udp.read(incoming, 20);
        incoming[len] = 0;
        
        if(strcmp(incoming, DISCOVERY_MSG) == 0){
            Serial.println("Discovery request received");
            udp.beginPacket(udp.remoteIP(), udp.remotePort());
            udp.write((const uint8_t*)RESPONSE_MSG, strlen(RESPONSE_MSG)); 
            udp.endPacket();
        }
    }

    vTaskDelay(1000 / portTICK_PERIOD_MS);
}

// ================== Tasks ==================

void mqttTask(void *pvParameters){
    // Task for PicoMQTT (Local Broker)
    for(;;){
        mqtt.loop();
        vTaskDelay(10 / portTICK_PERIOD_MS);
    }
}

void cloudMqttTask(void *pvParameters) {
    // Task for PubSubClient (Cloud Client)
    for(;;) {
        if (WiFi.status() == WL_CONNECTED && !cloudClient.connected()) {
            reconnectCloudMQTT();
        }
        cloudClient.loop();
        vTaskDelay(60000 / portTICK_PERIOD_MS); // เช็คการเชื่อมต่อ Cloud ทุก 1 นาที
    }
}

void dataTask(void *pvParameters){
    // ... (โค้ดเดิมสำหรับ Data Aggregation) ...
    for(;;){
        unsigned long now = millis();
        
        // อัพเดท duration ของค่าปัจจุบันก่อนประมวลผลข้อมูลใหม่
        if(!isFirstValue && lastUpdateTime > 0){
            unsigned long elapsed = now - lastUpdateTime;
            valueDuration += elapsed;
        }
        lastUpdateTime = now;
        
        // รับค่าทุกตัวจาก queue
        while(xQueueReceive(dataQueue, &value, 0) == pdTRUE){
            
            if(value != currentValue){
                // ถ้ามีค่าเดิม → เช็คว่า duration นานที่สุดหรือไม่
                if(!isFirstValue && currentValue >= 0){
                    if(valueDuration > longestDuration){
                        longestDuration = valueDuration;
                        longestValue = currentValue;
                    }
                }
                // อัพเดทค่าใหม่และ reset duration
                currentValue = value;
                valueDuration = 0;
                isFirstValue = false;
            }
            vTaskDelay(2);
        }

        // ครบ 1 นาที → process longest value
        if(millis() - startTime >= ONE_MINUTE){
            processMinuteData();
            
            // Reset สำหรับรอบใหม่
            runningSum = 0;
            runningCount = 0;
            startTime = millis();
            // Reset duration tracking
            valueDuration = 0;
            lastUpdateTime = millis();
            isFirstValue = true;
        }
        vTaskDelay(50 / portTICK_PERIOD_MS);
    }
}

void wifiMemoryTask(void *pvParameters){
    // ... (โค้ดเดิมสำหรับ WiFi/Memory Check) ...
    for(;;){
        // เช็ค WiFi ทุก 5 นาที
        if(WiFi.status() != WL_CONNECTED) {
            connectWiFi();
        }

        // ตรวจสอบ Heap
        size_t freeHeap = ESP.getFreeHeap();
        // ถ้า heap เหลือน้อย → clear queue
        if(freeHeap < 20000) { // เหลือ <20KB
            xQueueReset(dataQueue);
        }
        vTaskDelay(300000 / portTICK_PERIOD_MS); // เช็คทุก 5 นาที
    }
}


// ================== Functions ==================

void reconnectCloudMQTT() {
    Serial.println("Attempting Cloud MQTT connection...");
    // Loop จนกว่าจะเชื่อมต่อได้
    if (cloudClient.connect(CLOUD_MQTT_CLIENT_ID)) {
        Serial.println("Cloud MQTT connected!");
    } else {
        Serial.print("Cloud MQTT failed, rc=");
        Serial.print(cloudClient.state());
        Serial.println(" Retrying in 5 seconds");
        // หน่วงเวลาถูกจัดการใน cloudMqttTask แล้ว
    }
}

void connectWiFi(){
    WiFiManager wm;
    WiFi.mode(WIFI_STA);
    
    wm.setConfigPortalTimeout(180); // 3 นาที

    if(!wm.autoConnect("LD2460_Broker_Setup")) {
        Serial.println("Failed to connect and timed out. Restarting...");
        ESP.restart();
    }

    Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
    Serial.printf("Connected to SSID: %s\n", WiFi.SSID().c_str());

    udp.begin(DISCOVERY_PORT);
    Serial.printf("UDP Discovery listening on port %d\n", DISCOVERY_PORT);
}

void processMinuteData(){
    if(!isFirstValue && currentValue >= 0){
        if(valueDuration > longestDuration){
            longestDuration = valueDuration;
            longestValue = currentValue;
        }
    }
    
    if(longestValue >= 0 && longestValue != avg_old){
        sendToInfluxDB(longestValue);
        avg_old = longestValue;
    }

    // reset สำหรับรอบถัดไป
    currentValue = -1;
    currentDuration = 0;
    longestValue = -1;
    longestDuration = 0;
}


void sendToInfluxDB(int average_count){
    if(WiFi.status() != WL_CONNECTED) return;

    HTTPClient http;
    String lineProtocol = String(measurement_name) + ",Processor_Node=" + String(device_tag) + " count=" + String(average_count);
    String fullUrl = String(influx_url) + "?org=" + String(influx_org) + "&bucket=" + String(influx_bucket);
    http.begin(fullUrl);
    http.addHeader("Authorization", "Token " + String(influx_token));
    http.addHeader("Content-Type", "text/plain; charset=utf-8");
    int httpCode = http.POST(lineProtocol);
    yield();
    
    if (httpCode > 0) {
        if (httpCode == 204) {
            Serial.println(" Data sent successfully to InfluxDB!");
        } else {
            Serial.printf(" HTTP Error: %d\n", httpCode);
            String response = http.getString();
            if (response.length() > 0) {
                Serial.println("Response: " + response);
            }
        }
    } else {
        Serial.printf(" HTTP Request Failed: %s\n", http.errorToString(httpCode).c_str());
    }
    
    http.end();
}
/*
void loadCustomParams() {
    preferences.begin("mqtt_cfg", true); // "mqtt_cfg" คือ namespace
    String user = preferences.getString("user", "");
    String pass = preferences.getString("pass", "");
    preferences.end();
    
    // Copy string to fixed-size array
    if (user.length() > 0) {
        user.toCharArray(mqtt_user, 40);
        pass.toCharArray(mqtt_pass, 40);
        Serial.printf("Loaded MQTT User: %s\n", mqtt_user);
    }
}

void saveCustomParams() {
    preferences.begin("mqtt_cfg", false);
    preferences.putString("user", mqtt_user);
    preferences.putString("pass", mqtt_pass);
    preferences.end();
    Serial.println("Saved MQTT Credentials to NVS.");
}
*/