# PowerPOS — Odoo 20 Addons Installation, Upgrade & Configuration Guide

**Document ID:** `PPOS-INST-ODOO-001`  
**Target Version:** `20.0.1.0.0`

---

## 1. Addon Installation Order

Copy `odoo_addons/*` into your Odoo `addons_path` and install the modules in dependency order:

```bash
odoo-bin -c /etc/odoo/odoo.conf -d powerpos_prod -i \
  powerpos_sunmi_core,\
  powerpos_sunmi_cloud,\
  powerpos_sunmi_cashdrawer,\
  powerpos_sunmi_audit,\
  powerpos_sunmi_monitor,\
  powerpos_sunmi_pos,\
  powerpos_sunmi_reports \
  --stop-after-init
```

## 2. Initial Setup Workflow

1. **Assign Security Groups (`Settings -> Users & Companies -> Users`):**
   - Assign **PowerPOS / System Administrator** to hardware admins.
   - Assign **PowerPOS / Store Manager** to branch managers.
   - Assign **PowerPOS / Supervisor** to shift supervisors.
   - Assign **PowerPOS / Cashier** to POS cashiers.
2. **Create Store Branches (`PowerPOS -> Hardware Management -> Configuration -> Branches`):**
   - Configure branch code, company, prolonged open alert threshold (e.g. `30s`), and excessive opening threshold.
3. **Configure SUNMI Cloud Credentials (`PowerPOS -> Hardware Management -> Configuration -> SUNMI Cloud Accounts`):**
   - Enter `App ID` and `App Key` obtained from SUNMI Partner Portal (`https://openapi.sunmi.com`).
4. **Register Printers & Cash Drawers (`PowerPOS -> Hardware Management -> Registered Devices`):**
   - Register the SUNMI Printer (`powerpos.sunmi.device`) and paired Cash Drawer (`powerpos.cashdrawer.device`).
   - Run the **3-Step Hardware Calibration Wizard** to verify whether physical status sensing (`DLE EOT 1` / Android AIDL / External Sensor) is supported before enabling physical monitoring in production.

