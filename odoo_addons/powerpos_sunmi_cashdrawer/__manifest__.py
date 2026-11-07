{
    'name': 'PowerPOS SUNMI Cash Drawer - Physical State Monitor & Policy Engine',
    'version': '20.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Cash drawer device registry, opening authorization policy engine, and truthful physical state transition monitor',
    'description': """
PowerPOS SUNMI Cash Drawer (Module D & E)
=========================================
- Cash Drawer Device registry with RJ11 Pin-3 / GPIO sensor capability tracking
- Configurable Cash Drawer Authorization Policy per Branch / POS Register
- Pre-execution Opening Authorization Token Registry with 5-second correlation window
- Deterministic Physical State Transition Engine (UNKNOWN, CLOSED, OPEN, STALE, OFFLINE, ERROR, UNSUPPORTED)
- Strictly never fabricates a CLOSED event when telemetry becomes STALE/OFFLINE while OPEN
    """,
    'author': 'PowerPOS Enterprise Architecture Team',
    'website': 'https://powerpos.enterprise',
    'license': 'OEEL-1',
    'depends': ['powerpos_sunmi_core', 'powerpos_sunmi_cloud', 'hr'],
    'data': [
        'security/ir.access.csv',
        'data/ir_sequence_data.xml',
        'views/powerpos_cashdrawer_device_views.xml',
        'views/powerpos_cashdrawer_policy_views.xml',
        'views/powerpos_cashdrawer_authorization_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
