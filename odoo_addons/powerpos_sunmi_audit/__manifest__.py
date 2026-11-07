{
    'name': 'PowerPOS SUNMI Audit - Immutable Cryptographic Drawer Event Ledger',
    'version': '20.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Append-only SHA-256 hash-chained cash drawer audit trail and HMAC-authenticated hardware REST API',
    'description': """
PowerPOS SUNMI Audit (Module F)
===============================
- Append-only cash drawer event log with SHA-256 cryptographic hash chaining per drawer
- Strict ORM write() and unlink() protection preventing tampering with telemetry fields
- Supervisor review workflow for unmatched openings and prolonged open exceptions
- Authenticated Hardware & Cash Drawer REST API endpoints (/api/v1/hardware/*, /api/v1/drawer/*)
    """,
    'author': 'PowerPOS Enterprise Architecture Team',
    'website': 'https://powerpos.enterprise',
    'license': 'OEEL-1',
    'depends': ['powerpos_sunmi_core', 'powerpos_sunmi_cashdrawer'],
    'data': [
        'security/ir.access.csv',
        'views/powerpos_cashdrawer_event_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
