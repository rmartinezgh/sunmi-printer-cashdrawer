/** @odoo-module **/

import { Component, useState, onMounted } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { Navbar } from "@point_of_sale/app/navbar/navbar";
import { patch } from "@web/core/utils/patch";

export class PowerPosHardwareStatusWidget extends Component {
    static template = "powerpos_sunmi_pos.PowerPosHardwareStatusWidget";
    static props = {};

    setup() {
        this.hwService = useService("powerpos_hardware_status");
        this.pos = useService("pos");
        this.notification = useService("notification");
        this.uiState = useState({
            showDropdown: false,
            activeDialog: null, // 'supervisor' | 'recent_activity' | 'diagnostics'
            supervisorUserId: "",
            supervisorPin: "",
            supervisorReason: "",
        });

        onMounted(() => {
            const sessionId = this.pos?.session?.id || null;
            const configId = this.pos?.config?.id || null;
            if (sessionId) {
                this.hwService.initializeForSession(sessionId, configId);
            }
        });
    }

    get hw() {
        return this.hwService.state;
    }

    get printerBadgeClass() {
        const st = this.hw.printer.device_status;
        const op = this.hw.printer.printer_operational_status;
        if (st === "online" && op === "ready") {
            return "pp-badge-success";
        }
        if (op === "printing") {
            return "pp-badge-info";
        }
        if (op === "paper_near_end" || st === "degraded") {
            return "pp-badge-warning";
        }
        return "pp-badge-danger";
    }

    get drawerBadgeClass() {
        const pst = this.hw.drawer.physical_state;
        if (pst === "CLOSED") {
            return "pp-badge-success";
        }
        if (pst === "OPEN") {
            return this.hw.isProlongedOpenWarning ? "pp-badge-critical-pulse" : "pp-badge-danger";
        }
        if (pst === "UNKNOWN" || pst === "UNSUPPORTED" || pst === "STALE") {
            return "pp-badge-warning";
        }
        return "pp-badge-danger";
    }

    toggleDropdown() {
        this.uiState.showDropdown = !this.uiState.showDropdown;
        if (this.uiState.showDropdown) {
            this.hwService.refreshHardwareStatus();
        }
    }

    async onOpenDrawerStandard() {
        await this.hwService.requestDrawerOpen({
            reason: "cash_in",
            notes: "Cashier Drawer Request from POS Bar",
        });
    }

    openSupervisorModal() {
        this.uiState.activeDialog = "supervisor";
        this.uiState.supervisorPin = "";
        this.uiState.supervisorReason = "";
    }

    async submitSupervisorOverride() {
        if (!this.uiState.supervisorReason.trim()) {
            this.notification.add("Please enter a reason for Supervisor No-Sale opening.", { type: "warning" });
            return;
        }
        await this.hwService.verifySupervisorAndOpen({
            supervisorUserId: this.uiState.supervisorUserId || null,
            pinCode: this.uiState.supervisorPin,
            notes: this.uiState.supervisorReason,
        });
        this.uiState.activeDialog = null;
    }

    async onTestDrawerPulse() {
        await this.hwService.requestDrawerOpen({
            reason: "maintenance_test",
            notes: "POS Hardware Diagnostic Test Pulse",
        });
    }

    openRecentActivityModal() {
        this.hwService.refreshHardwareStatus();
        this.uiState.activeDialog = "recent_activity";
    }

    openDiagnosticsModal() {
        this.hwService.refreshHardwareStatus();
        this.uiState.activeDialog = "diagnostics";
    }

    closeModal() {
        this.uiState.activeDialog = null;
    }
}

patch(Navbar, {
    components: {
        ...Navbar.components,
        PowerPosHardwareStatusWidget,
    },
});
