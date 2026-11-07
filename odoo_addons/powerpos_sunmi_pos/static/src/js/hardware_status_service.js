/** @odoo-module **/

import { reactive } from "@odoo/owl";
import { registry } from "@web/core/registry";

const OFFLINE_QUEUE_KEY = "powerpos_sunmi_offline_drawer_events_v1";

export const powerposHardwareStatusService = {
    dependencies: ["orm", "bus_service", "notification"],

    start(env, { orm, bus_service, notification }) {
        const state = reactive({
            posSessionId: null,
            posConfigId: null,
            printer: {
                id: null,
                name: "Unassigned Printer",
                device_status: "offline",
                printer_operational_status: "unknown",
                paper_width: "80",
            },
            drawer: {
                id: null,
                drawer_code: "",
                name: "Unassigned Drawer",
                physical_state: "UNKNOWN",
                physical_status_capability: "UNVERIFIED",
                last_sensor_timestamp: null,
                open_since_timestamp: null,
                alert_prolonged_open_seconds: 30,
            },
            liveOpenSeconds: 0,
            isProlongedOpenWarning: false,
            recentEvents: [],
            offlineQueueCount: 0,
            panelOpen: false,
            activeModal: null, // 'supervisor', 'history', 'diagnostics'
        });

        let timerHandle = null;

        function loadOfflineQueue() {
            try {
                const raw = window.localStorage.getItem(OFFLINE_QUEUE_KEY);
                const parsed = raw ? JSON.parse(raw) : [];
                state.offlineQueueCount = Array.isArray(parsed) ? parsed.length : 0;
                return Array.isArray(parsed) ? parsed : [];
            } catch {
                return [];
            }
        }

        function enqueueOfflineEvent(evPayload) {
            const queue = loadOfflineQueue();
            queue.push(evPayload);
            try {
                window.localStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(queue));
            } catch {
                // Storage quota guard
            }
            state.offlineQueueCount = queue.length;
        }

        async function flushOfflineQueue() {
            const queue = loadOfflineQueue();
            if (!queue.length) {
                return;
            }
            try {
                const resp = await fetch("/api/v1/drawer/events", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ events: queue }),
                });
                if (resp.ok) {
                    window.localStorage.removeItem(OFFLINE_QUEUE_KEY);
                    state.offlineQueueCount = 0;
                }
            } catch {
                // Keep queued for next reconnect cycle
            }
        }

        function updateLiveTimer() {
            if (state.drawer.physical_state === "OPEN" && state.drawer.open_since_timestamp) {
                const openedAt = new Date(state.drawer.open_since_timestamp.replace(" ", "T") + "Z").getTime();
                const nowMs = Date.now();
                const elapsed = Math.max(0, Math.floor((nowMs - openedAt) / 1000));
                state.liveOpenSeconds = isNaN(elapsed) ? state.liveOpenSeconds + 1 : elapsed;
                const threshold = state.drawer.alert_prolonged_open_seconds || 30;
                state.isProlongedOpenWarning = state.liveOpenSeconds >= threshold;
            } else {
                state.liveOpenSeconds = 0;
                state.isProlongedOpenWarning = false;
            }
        }

        function bindAndroidBridgeCallbacks() {
            window.onPowerPosHardwareTelemetry = (telemetry) => {
                if (!telemetry) {
                    return;
                }
                if (telemetry.printer_operational_status) {
                    state.printer.printer_operational_status = telemetry.printer_operational_status;
                    state.printer.device_status = telemetry.device_status || "online";
                }
                if (telemetry.physical_state) {
                    const prev = state.drawer.physical_state;
                    state.drawer.physical_state = telemetry.physical_state;
                    state.drawer.last_sensor_timestamp = telemetry.timestamp || new Date().toISOString();
                    if (telemetry.physical_state === "OPEN" && prev !== "OPEN") {
                        state.drawer.open_since_timestamp = state.drawer.last_sensor_timestamp;
                    } else if (telemetry.physical_state === "CLOSED") {
                        state.drawer.open_since_timestamp = null;
                        state.liveOpenSeconds = 0;
                        state.isProlongedOpenWarning = false;
                    }
                }
            };
        }

        async function initializeForSession(posSessionId, posConfigId) {
            state.posSessionId = posSessionId;
            state.posConfigId = posConfigId;
            bindAndroidBridgeCallbacks();
            loadOfflineQueue();

            if (posConfigId && bus_service) {
                const channel = `powerpos_hardware_${posConfigId}`;
                bus_service.addChannel(channel);
                bus_service.subscribe("powerpos_hardware_event", (payload) => {
                    if (payload.type === "drawer_state_change" && payload.drawer) {
                        Object.assign(state.drawer, payload.drawer);
                        updateLiveTimer();
                    } else if (payload.type === "heartbeat" && payload.device) {
                        Object.assign(state.printer, payload.device);
                    } else if (payload.type === "security_alert") {
                        notification.add(`${payload.title}: ${payload.description}`, {
                            title: "PowerPOS Cash Drawer Security Alert",
                            type: payload.severity === "critical" ? "danger" : "warning",
                            sticky: payload.severity === "critical",
                        });
                    }
                });
            }

            if (!timerHandle) {
                timerHandle = window.setInterval(() => {
                    updateLiveTimer();
                }, 1000);
            }

            await refreshHardwareStatus();
            await flushOfflineQueue();
        }

        async function refreshHardwareStatus() {
            if (!state.posSessionId) {
                return;
            }
            try {
                const data = await orm.call(
                    "pos.session",
                    "powerpos_get_hardware_status",
                    [[state.posSessionId]]
                );
                if (data.printer) {
                    Object.assign(state.printer, data.printer);
                }
                if (data.drawer) {
                    Object.assign(state.drawer, data.drawer);
                }
                if (Array.isArray(data.recent_events)) {
                    state.recentEvents = data.recent_events;
                }
                updateLiveTimer();
            } catch {
                // Offline fallback: retain last known state or mark offline
                state.printer.device_status = "offline";
            }
        }

        async function requestDrawerOpen({ reason = "cash_sale", notes = "", supervisorId = false, posOrderId = false, cashierId = false } = {}) {
            const idempotencyKey = `pos-${state.posSessionId || 0}-${Date.now()}`;
            try {
                const authResult = await orm.call(
                    "pos.session",
                    "powerpos_request_drawer_open",
                    [[state.posSessionId]],
                    {
                        reason,
                        notes,
                        supervisor_id: supervisorId,
                        pos_order_id: posOrderId,
                        cashier_id: cashierId,
                        idempotency_key: idempotencyKey,
                    }
                );

                // Trigger local SUNMI Android Bridge if running inside SUNMI terminal WebView
                if (window.PowerPosAndroidBridge && typeof window.PowerPosAndroidBridge.openCashDrawer === "function") {
                    window.PowerPosAndroidBridge.openCashDrawer(JSON.stringify({
                        auth_uuid: authResult.auth_uuid,
                        drawer_code: authResult.drawer_code,
                        reason,
                    }));
                }

                notification.add(`Cash drawer kick authorized (${reason}). Waiting for physical sensor verification...`, {
                    type: "info",
                });
                await refreshHardwareStatus();
                return authResult;
            } catch (err) {
                if (!navigator.onLine && window.PowerPosAndroidBridge) {
                    const offlineEvent = {
                        event_uuid: idempotencyKey,
                        drawer_id: state.drawer.id,
                        drawer_code: state.drawer.drawer_code,
                        new_physical_state: "OPEN",
                        opening_reason: reason,
                        device_observed_timestamp: new Date().toISOString().slice(0, 19).replace("T", " "),
                        event_source: "android_bridge",
                    };
                    enqueueOfflineEvent(offlineEvent);
                    window.PowerPosAndroidBridge.openCashDrawer(JSON.stringify({
                        auth_uuid: `OFFLINE-${idempotencyKey}`,
                        drawer_code: state.drawer.drawer_code,
                        reason,
                    }));
                    return { authorized: true, offline: true };
                }
                notification.add(err.message || "Drawer opening rejected by security policy.", {
                    title: "Cash Drawer Access Denied",
                    type: "danger",
                });
                throw err;
            }
        }

        async function verifySupervisorAndOpen({ supervisorUserId, pinCode, notes }) {
            const verifyRes = await orm.call(
                "pos.session",
                "powerpos_verify_supervisor_pin",
                [[state.posSessionId], supervisorUserId, pinCode]
            );
            if (verifyRes && verifyRes.verified) {
                return await requestDrawerOpen({
                    reason: "supervisor_no_sale",
                    notes: notes || "Supervisor No-Sale Override",
                    supervisorId: verifyRes.supervisor_id,
                });
            }
        }

        return {
            state,
            initializeForSession,
            refreshHardwareStatus,
            requestDrawerOpen,
            verifySupervisorAndOpen,
            flushOfflineQueue,
        };
    },
};

registry.category("services").add("powerpos_hardware_status", powerposHardwareStatusService);
