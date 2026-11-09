#pragma once

// Copy this file to config.h. Do not commit config.h.
constexpr char WIFI_SSID[] = "YOUR_WIFI";
constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";
constexpr char BACKEND_URL[] = "http://192.168.1.100:8000/api/v1";
constexpr char GATEWAY_ID[] = "GATEWAY_001";
constexpr char GATEWAY_KEY[] = "gateway-local-key";
constexpr char NODE_SHARED_KEY[] = "node-local-key";
constexpr char AP_SSID[] = "FarmGateway";
constexpr char AP_PASSWORD[] = "FarmGateway123";
constexpr size_t MAX_QUEUE_RECORDS = 100;
constexpr unsigned long SYNC_INTERVAL_MS = 15000;
constexpr unsigned long MISSION_REFRESH_INTERVAL_MS = 30000;
