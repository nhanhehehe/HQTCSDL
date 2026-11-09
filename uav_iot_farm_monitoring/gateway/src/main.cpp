#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <WebServer.h>
#include <WiFi.h>

#include "config.h"

WebServer server(80);
const char* QUEUE_FILE = "/queue.json";
unsigned long lastSyncAt = 0;
unsigned long lastMissionAt = 0;
int activeMissionId = 0;

bool writeQueue(JsonArray queue) {
  File file = LittleFS.open(QUEUE_FILE, FILE_WRITE);
  if (!file) return false;
  serializeJson(queue, file);
  file.close();
  return true;
}

bool loadQueue(JsonDocument& document) {
  document.to<JsonArray>();
  if (!LittleFS.exists(QUEUE_FILE)) return true;
  File file = LittleFS.open(QUEUE_FILE, FILE_READ);
  if (!file) return false;
  DeserializationError error = deserializeJson(document, file);
  file.close();
  return !error && document.is<JsonArray>();
}

bool enqueueRecord(JsonObject record) {
  JsonDocument queueDocument;
  if (!loadQueue(queueDocument)) return false;
  JsonArray queue = queueDocument.as<JsonArray>();
  if (queue.size() >= MAX_QUEUE_RECORDS) return false;
  JsonObject copy = queue.add<JsonObject>();
  copy.set(record);
  return writeQueue(queue);
}

bool nodeKeyValid() {
  return server.hasHeader("X-Node-Key") && server.header("X-Node-Key") == NODE_SHARED_KEY;
}

bool measurementValid(JsonObject record, String& reason) {
  const char* required[] = {"sensor_id", "sequence_no", "temperature", "air_humidity", "pressure", "light", "soil_moisture", "measured_at"};
  for (const char* field : required) {
    if (!record.containsKey(field)) { reason = String("missing_") + field; return false; }
  }
  if (record["temperature"].as<float>() < -40 || record["temperature"].as<float>() > 85) { reason = "temperature_range"; return false; }
  if (record["air_humidity"].as<float>() < 0 || record["air_humidity"].as<float>() > 100) { reason = "humidity_range"; return false; }
  if (record["pressure"].as<float>() < 300 || record["pressure"].as<float>() > 1100) { reason = "pressure_range"; return false; }
  if (record["light"].as<float>() < 0 || record["light"].as<float>() > 65535) { reason = "light_range"; return false; }
  if (record["soil_moisture"].as<float>() < 0 || record["soil_moisture"].as<float>() > 100) { reason = "soil_moisture_range"; return false; }
  return true;
}

void handleDiscover() {
  if (!nodeKeyValid()) { server.send(401, "application/json", "{\"status\":\"unauthorized\"}"); return; }
  server.send(200, "application/json", "{\"status\":\"accepted\",\"gateway_id\":\"" + String(GATEWAY_ID) + "\"}");
}

void handleMeasurement() {
  if (!nodeKeyValid()) { server.send(401, "application/json", "{\"status\":\"unauthorized\"}"); return; }
  JsonDocument input;
  if (deserializeJson(input, server.arg("plain"))) { server.send(400, "application/json", "{\"status\":\"invalid\",\"reason\":\"invalid_json\"}"); return; }
  String reason;
  JsonObject record = input.as<JsonObject>();
  if (!measurementValid(record, reason)) { server.send(422, "application/json", "{\"status\":\"invalid\",\"reason\":\"" + reason + "\"}"); return; }
  if (!enqueueRecord(record)) { server.send(507, "application/json", "{\"status\":\"queue_full\"}"); return; }
  Serial.printf("[QUEUE] stored sensor=%s sequence=%d\n", record["sensor_id"].as<const char*>(), record["sequence_no"].as<int>());
  server.send(201, "application/json", "{\"status\":\"collected\"}");
}

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 10000) delay(250);
  Serial.println(WiFi.status() == WL_CONNECTED ? "[WIFI] connected" : "[WIFI] offline");
}

void syncQueue() {
  if (WiFi.status() != WL_CONNECTED) return;
  JsonDocument queueDocument;
  if (!loadQueue(queueDocument)) { Serial.println("[QUEUE] could not load"); return; }
  JsonArray queue = queueDocument.as<JsonArray>();
  if (queue.isNull() || queue.size() == 0) return;

  JsonDocument payload;
  payload["gateway_id"] = GATEWAY_ID;
  JsonArray records = payload["records"].to<JsonArray>();
  for (JsonObject record : queue) records.add(record);
  String body;
  serializeJson(payload, body);

  HTTPClient http;
  http.begin(String(BACKEND_URL) + "/sync/batches");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Gateway-Key", GATEWAY_KEY);
  int status = http.POST(body);
  if (status != 200) { Serial.printf("[SYNC] backend status=%d\n", status); http.end(); return; }

  JsonDocument response;
  DeserializationError error = deserializeJson(response, http.getString());
  http.end();
  if (error) { Serial.println("[SYNC] invalid ACK"); return; }
  JsonArray acks = response["acknowledgements"].as<JsonArray>();
  JsonDocument remainingDocument;
  JsonArray remaining = remainingDocument.to<JsonArray>();
  for (size_t index = 0; index < queue.size(); index++) {
    const char* ack = acks[index]["status"] | "retry";
    if (String(ack) != "accepted" && String(ack) != "duplicate" && String(ack) != "invalid") remaining.add(queue[index]);
  }
  writeQueue(remaining);
  Serial.printf("[SYNC] acknowledged=%d remaining=%d\n", queue.size() - remaining.size(), remaining.size());
}

void pullActiveMission() {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.begin(String(BACKEND_URL) + "/gateways/" + GATEWAY_ID + "/active-mission");
  http.addHeader("X-Gateway-Key", GATEWAY_KEY);
  int status = http.GET();
  if (status != 200) { Serial.printf("[MISSION] backend status=%d\n", status); http.end(); return; }
  JsonDocument response;
  DeserializationError error = deserializeJson(response, http.getString());
  http.end();
  if (error) { Serial.println("[MISSION] invalid response"); return; }
  JsonVariant mission = response["mission"];
  if (mission.isNull()) { activeMissionId = 0; Serial.println("[MISSION] no active mission"); return; }
  activeMissionId = mission["id"] | 0;
  JsonArray points = mission["points"].as<JsonArray>();
  Serial.printf("[MISSION] id=%d points=%d\n", activeMissionId, points.size());
  for (JsonObject point : points) {
    Serial.printf("[MISSION] %d: %s (%s)\n", point["sort_order"].as<int>(), point["sensor_id"].as<const char*>(), point["status"].as<const char*>());
  }
}

void setup() {
  Serial.begin(115200);
  LittleFS.begin(true);
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(AP_SSID, AP_PASSWORD);
  connectWifi();
  pullActiveMission();
  const char* headers[] = {"X-Node-Key"};
  server.collectHeaders(headers, 1);
  server.on("/node/discover", HTTP_POST, handleDiscover);
  server.on("/node/measurements", HTTP_POST, handleMeasurement);
  server.onNotFound([]() { server.send(404, "application/json", "{\"status\":\"not_found\"}"); });
  server.begin();
  Serial.printf("[BOOT] gateway AP=%s IP=%s\n", AP_SSID, WiFi.softAPIP().toString().c_str());
}

void loop() {
  server.handleClient();
  if (millis() - lastSyncAt >= SYNC_INTERVAL_MS) {
    lastSyncAt = millis();
    connectWifi();
    syncQueue();
  }
  if (millis() - lastMissionAt >= MISSION_REFRESH_INTERVAL_MS) {
    lastMissionAt = millis();
    connectWifi();
    pullActiveMission();
  }
}
