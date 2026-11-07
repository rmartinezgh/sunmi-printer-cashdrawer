{
    'name': 'PowerPOS SUNMI POS - OWL 2 Real-Time Hardware & Cash Drawer UI',
    'version': '18.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Touch-friendly OWL 2 POS navbar widget, live open duration timer, Android Bridge integration, and offline event queue',
    'description': """
PowerPOS SUNMI POS (Module H)
=============================
- Loads sanitized SUNMI printer & cash drawer metadata into POS session (strictly excluding app_key, device_secret, auth_token_hash)
- Reactive OWL 2 hardware_status_service subscribing to bus_service and window.PowerPosAndroidBridge
- Touch-friendly POS navbar widget showing live Printer Status, verified Cash Drawer Physical State, Last Verified Timestamp, and Live Elapsed Open Duration timer
- Built-in Drawer Controls: Open Drawer, Supervisor Opening (PIN verified), Test Drawer, Recent Activity, and Diagnostics
    """,
    'author': 'PowerPOS Enterprise Architecture Team',
    'website': 'https://powerpos.enterprise',
    'license': 'OEEL-1',
    'depends': [
        'point_of_sale',
        'powerpos_sunmi_core',
        'powerpos_sunmi_cloud',
        'powerpos_sunmi_cashdrawer',
        'powerpos_sunmi_audit',
        'powerpos_sunmi_monitor',
    ],
    'data': [
        'views/pos_config_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'powerpos_sunmi_pos/static/src/css/powerpos_pos.css',
            'powerpos_sunmi_pos/static/src/js/hardware_status_service.js',
            'powerpos_sunmi_pos/static/src/js/hardware_status_widget.js',
            'powerpos_sunmi_pos/static/src/xml/hardware_status_widget.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
