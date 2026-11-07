{
    'name': 'PowerPOS SUNMI Core - Device Management & Security Foundation',
    'version': '20.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Multi-branch device registry, RBAC security foundation, and hardware capability matrix for SUNMI POS',
    'description': """
PowerPOS SUNMI Core (Module A)
==============================
- Multi-tier security role hierarchy (Cashier, Supervisor, Store Manager, System Administrator)
- Multi-company and multi-branch hardware asset isolation
- SUNMI built-in, USB, network, and cloud printer device management
- Hardware capability discovery matrix and 3-step physical sensor polarity calibration wizard
    """,
    'author': 'PowerPOS Enterprise Architecture Team',
    'website': 'https://powerpos.enterprise',
    'license': 'OEEL-1',
    'depends': ['base', 'mail', 'point_of_sale', 'bus'],
    'data': [
        'security/security.xml',
        'security/ir.access.csv',
        'data/ir_sequence_data.xml',
        'views/powerpos_branch_views.xml',
        'views/powerpos_sunmi_device_views.xml',
        'wizards/hardware_calibration_wizard_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
