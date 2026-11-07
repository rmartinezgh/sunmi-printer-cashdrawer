{
    'name': 'PowerPOS SUNMI Reports - Cash Drawer & Hardware Analytics Suite',
    'version': '20.0.1.0.0',
    'category': 'Point of Sale',
    'summary': '8 Enterprise Reports with PDF QWeb, CSV, and XLSX exports for Cash Drawer & SUNMI Printer auditing',
    'description': """
PowerPOS SUNMI Reports (Module K)
=================================
Implements all 8 enterprise hardware and security audit reports with QWeb PDF, CSV, and XLSX exports:
1. Daily Drawer Activity Report
2. Cashier Drawer Activity Report
3. Drawer Opening Duration Report
4. Unmatched Drawer Opening Report
5. Supervisor Override Report
6. Printer Performance Report
7. Device Connectivity Report
8. Cash Drawer Exception Report
    """,
    'author': 'PowerPOS Enterprise Architecture Team',
    'website': 'https://powerpos.enterprise',
    'license': 'OEEL-1',
    'depends': [
        'powerpos_sunmi_core',
        'powerpos_sunmi_cloud',
        'powerpos_sunmi_cashdrawer',
        'powerpos_sunmi_audit',
        'powerpos_sunmi_monitor',
    ],
    'data': [
        'security/ir.model.access.csv',
        'report/powerpos_report_actions.xml',
        'report/powerpos_report_templates.xml',
        'wizards/powerpos_report_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
