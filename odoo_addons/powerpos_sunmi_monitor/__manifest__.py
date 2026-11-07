{
    'name': 'PowerPOS SUNMI Monitor - Security Alerts & Central Hardware Dashboard',
    'version': '18.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Intelligent security alert engine, 11-KPI central hardware dashboard, and 12-submenu Hardware Management hub',
    'description': """
PowerPOS SUNMI Monitor (Module G & J)
=====================================
- Configurable Monitoring Rules (Drawer Left Open 15s/30s/60s/120s, Unmatched Opening, Excessive Manual/No-Sale Openings, Outside-Session Openings, Offline/Stale Sensor)
- Security Alert Lifecycle (Active, Acknowledged, Investigating, Resolved) with Supervisor audit tracking
- Central Hardware Dashboard computing all 11 verified KPIs and real-time bus.bus push notifications
- Complete PowerPOS -> Hardware Management 12-submenu navigation hierarchy
    """,
    'author': 'PowerPOS Enterprise Architecture Team',
    'website': 'https://powerpos.enterprise',
    'license': 'OEEL-1',
    'depends': [
        'powerpos_sunmi_core',
        'powerpos_sunmi_cloud',
        'powerpos_sunmi_cashdrawer',
        'powerpos_sunmi_audit',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/monitoring_rule_data.xml',
        'views/powerpos_monitoring_rule_views.xml',
        'views/powerpos_security_alert_views.xml',
        'views/powerpos_hardware_dashboard_views.xml',
        'views/powerpos_menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
