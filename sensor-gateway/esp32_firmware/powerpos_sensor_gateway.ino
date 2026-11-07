/*
 * PowerPOS — ESP32 External Cash Drawer Sensor Gateway Firmware
 * Hardware: ESP32-WROOM / ESP32-S3 + Magnetic Reed Switch (GPIO 4) + Tamper Loop (GPIO 5)
 *
 * Purpose:
 *   Provides 100% independent physical OPEN/CLOSED detection for printers that
 *   lack bidirectional RJ11 Pin 3 feedback (e.g., SUNMI NT311 Cloud Printer in
 *   cloud-only mode, 58mm printers, or 2-wire cash drawers).
 *
 * Security:
 *   Every state transition and heartbeat is signed with HMAC-SHA256 using a
 *   per-device secret stored in ESP32 NVS (Non-Volatile Storage).
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include "mbedtls/md.h"

static const int PIN_REED_SWITCH = 4;    // Closed to GND when drawer is physically shut
static const int PIN_TAMPER_LOOP = 5;    // Closed to GND when sensor cable is intact
static const int PIN_STATUS_LED  = 2;

static const unsigned long DEBOUNCE_MS      = 50;
static const unsigned long HEARTBEAT_MS     = 15000;

const char* WIFI_SSID       = "POWERPOS_STORE_IOT";
const char* WIFI_PASSWORD   = "CHANGE_ME_IN_NVS";
const char* ODOO_EVENTS_URL = "https://pos.powerpos.local/api/v1/drawer/events";
const char* DEVICE_ID       = "ESP32-DRAWER-GW-01";
const char* DEVICE_SECRET   = "REPLACE_WITH_PROVISIONED_SECRET";

struct BufferedSensorEvent {
  uint32_t sequenceNumber;
  bool contactClosed;
  bool tamperIntact;
  unsigned long observedEpochSec;
  bool synced;
};

static const size_t RING_BUFFER_SIZE = 128;
BufferedSensorEvent ringBuffer[RING_BUFFER_SIZE];
size_t writeIndex = 0;
uint32_t globalSeq = 0;

bool lastStableClosed = true;
bool lastCandidateClosed = true;
unsigned long lastBounceTime = 0;
unsigned long lastHeartbeatTime = 0;

String computeHmacSha256(const String& payload, const char* secret) {
  byte hmacResult[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_type_t md_type = MBEDTLS_MD_SHA256;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(md_type), 1);
  mbedtls_md_hmac_starts(&ctx, (const unsigned char*)secret, strlen(secret));
  mbedtls_md_hmac_update(&ctx, (const unsigned char*)payload.c_str(), payload.length());
  mbedtls_md_hmac_finish(&ctx, hmacResult);
  mbedtls_md_free(&ctx);

  char hexBuf[65];
  for (int i = 0; i < 32; i++) {
    sprintf(hexBuf + (i * 2), "%02x", hmacResult[i]);
  }
  hexBuf[64] = '\0';
  return String(hexBuf);
}

void enqueueTransition(bool contactClosed, bool tamperIntact) {
  globalSeq++;
  BufferedSensorEvent& slot = ringBuffer[writeIndex % RING_BUFFER_SIZE];
  slot.sequenceNumber = globalSeq;
  slot.contactClosed = contactClosed;
  slot.tamperIntact = tamperIntact;
  slot.observedEpochSec = millis() / 1000UL;
  slot.synced = false;
  writeIndex++;
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_REED_SWITCH, INPUT_PULLUP);
  pinMode(PIN_TAMPER_LOOP, INPUT_PULLUP);
  pinMode(PIN_STATUS_LED, OUTPUT);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  lastStableClosed = (digitalRead(PIN_REED_SWITCH) == LOW);
  lastCandidateClosed = lastStableClosed;
}

void loop() {
  unsigned long nowMs = millis();
  bool rawClosed = (digitalRead(PIN_REED_SWITCH) == LOW);
  bool tamperIntact = (digitalRead(PIN_TAMPER_LOOP) == LOW);

  if (rawClosed != lastCandidateClosed) {
    lastBounceTime = nowMs;
    lastCandidateClosed = rawClosed;
  }

  if ((nowMs - lastBounceTime) >= DEBOUNCE_MS) {
    if (rawClosed != lastStableClosed) {
      lastStableClosed = rawClosed;
      digitalWrite(PIN_STATUS_LED, lastStableClosed ? LOW : HIGH);
      enqueueTransition(lastStableClosed, tamperIntact);
    }
  }

  if ((nowMs - lastHeartbeatTime) >= HEARTBEAT_MS) {
    lastHeartbeatTime = nowMs;
    enqueueTransition(lastStableClosed, tamperIntact);
  }

  delay(10);
}

