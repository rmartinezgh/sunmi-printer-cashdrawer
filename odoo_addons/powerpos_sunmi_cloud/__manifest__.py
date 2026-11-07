{
    'name': 'PowerPOS SUNMI Cloud - OpenAPI Printer & Receipt Engine',
    'version': '20.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'SUNMI Cloud OpenAPI v2 HMAC-SHA256 client, idempotent print job engine, and Philippine BIR receipt templates',
    'description': """
PowerPOS SUNMI Cloud (Module B)
===============================
- Server-side SUNMI OpenAPI v2 authentication (Sunmi-Appid, Sunmi-Timestamp, Sunmi-Nonce, Sunmi-Sign)
- Complete 8-method SDK implementation (bind_shop, unbind_shop, online_status, clear_print_job, push_voice, push_content, print_status, verify_new_ticket_notify)
- Idempotent print job lifecycle with printStatus() reconciliation before retries so accepted jobs never double-print
- Philippine BIR compliant receipt template engine separating immutable tax fields from merchant headers/footers
    """,
    'author': 'PowerPOS Enterprise Architecture Team',
    'website': 'https://powerpos.enterprise',
    'license': 'OEEL-1',
    'depends': ['powerpos_sunmi_core'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/receipt_template_data.xml',
        'views/powerpos_sunmi_cloud_account_views.xml',
        'views/powerpos_sunmi_print_job_views.xml',
        'views/powerpos_sunmi_receipt_template_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
