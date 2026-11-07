from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class PowerPosSunmiReceiptTemplate(models.Model):
    _name = 'powerpos.sunmi.receipt.template'
    _description = 'Philippine BIR & Merchant SUNMI Receipt Template'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Template Name', required=True, tracking=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    branch_id = fields.Many2one(
        'powerpos.branch',
        string='Specific Branch (Optional)',
        domain="[('company_id', '=', company_id)]",
    )
    paper_width = fields.Selection(
        selection=[('58', '58 mm (32 cols)'), ('80', '80 mm (48 cols)')],
        string='Paper Width',
        default='80',
        required=True,
    )

    # Mandatory Philippine BIR Compliance Fields (SysAdmin Protected on write)
    tin = fields.Char(
        string='BIR Taxpayer Identification Number (TIN)',
        required=True,
        default='000-000-000-00000',
        tracking=True,
    )
    tax_regime = fields.Selection(
        selection=[
            ('vat', 'VAT Registered'),
            ('non_vat', 'Non-VAT Registered'),
        ],
        string='Tax Registration Type',
        default='vat',
        required=True,
        tracking=True,
    )
    branch_code = fields.Char(string='BIR Branch Code', default='00000', required=True, tracking=True)
    min_number = fields.Char(string='Machine Identification Number (MIN)', required=True, tracking=True)
    ptu_number = fields.Char(string='Permit to Use (PTU) Number', required=True, tracking=True)
    ptu_date_issued = fields.Date(string='PTU Date Issued', required=True, tracking=True)
    serial_no = fields.Char(string='Terminal Hardware Serial No.', required=True, tracking=True)
    accreditation_disclaimer = fields.Text(
        string='BIR Accreditation & Official Invoice Footer',
        required=True,
        default='THIS SERVES AS YOUR OFFICIAL SALES INVOICE.\nBIR ACCREDITATION NO.: 000-000000000-000000',
        tracking=True,
    )

    # Merchant Customizable Presentation Fields
    merchant_header_line1 = fields.Char(string='Merchant Trade Name', required=True, default='POWERPOS ENTERPRISE STORE')
    merchant_header_line2 = fields.Char(string='Store Address Line', default='Makati Avenue, Metro Manila')
    merchant_contact_line = fields.Char(string='Merchant Contact / Tel', default='Tel: (02) 8888-0000')
    merchant_footer_message = fields.Text(
        string='Promotional / Thank You Footer',
        default='Thank you for shopping with us! Please come again.',
    )
    print_barcode = fields.Boolean(string='Print Order Barcode', default=True)
    active = fields.Boolean(string='Active', default=True)

    _BIR_PROTECTED_FIELDS = {
        'tin', 'tax_regime', 'branch_code', 'min_number',
        'ptu_number', 'ptu_date_issued', 'serial_no', 'accreditation_disclaimer',
    }

    def write(self, vals):
        if self._BIR_PROTECTED_FIELDS.intersection(vals.keys()):
            if not self.env.user.has_group('powerpos_sunmi_core.group_powerpos_sysadmin'):
                raise AccessError(_(
                    'Mandatory Philippine BIR fiscal registration fields (TIN, MIN, PTU, Serial No, Accreditation) '
                    'can only be modified by a System Administrator.'
                ))
        return super().write(vals)

    @api.model
    def _text_to_escpos_hex(self, text_content, cut_paper=True):
        """Encodes formatted receipt text into standard ESC/POS hexadecimal instruction stream."""
        esc_init = b'\x1b\x40'  # Initialize printer
        body_bytes = text_content.encode('cp437', errors='replace') + b'\n\n\n'
        esc_cut = b'\x1d\x56\x42\x00' if cut_paper else b''
        return (esc_init + body_bytes + esc_cut).hex().upper()

    def render_receipt_escpos_hex(self, receipt_type='official_sales', order_data=None):
        """Generates ESC/POS hex stream combining merchant header, transaction lines, and immutable BIR footer."""
        self.ensure_one()
        order_data = order_data or {}
        cols = 48 if self.paper_width == '80' else 32
        sep = '-' * cols

        lines = [
            self.merchant_header_line1.center(cols),
            (self.merchant_header_line2 or '').center(cols),
            (self.merchant_contact_line or '').center(cols),
            f"{'VAT REG' if self.tax_regime == 'vat' else 'NON-VAT'} TIN: {self.tin}".center(cols),
            f"MIN: {self.min_number}".center(cols),
            f"SN: {self.serial_no}  BR: {self.branch_code}".center(cols),
            sep,
            f"DOC TYPE: {receipt_type.upper()}".ljust(cols),
            f"REF NO: {order_data.get('receipt_number', 'N/A')}".ljust(cols),
            f"DATE: {order_data.get('timestamp', fields.Datetime.to_string(fields.Datetime.now()))}".ljust(cols),
            f"CASHIER: {order_data.get('cashier', self.env.user.name)}".ljust(cols),
            sep,
        ]

        for item in order_data.get('lines', []):
            name = str(item.get('name', 'Item'))[:cols - 14]
            amt = f"{float(item.get('subtotal', 0.0)):.2f}"
            lines.append(f"{name.ljust(cols - len(amt))}{amt}")

        lines.append(sep)
        total_str = f"{float(order_data.get('amount_total', 0.0)):.2f}"
        lines.append(f"{'TOTAL PHP:'.ljust(cols - len(total_str))}{total_str}")

        if self.tax_regime == 'vat':
            vatable = float(order_data.get('vatable_sales', 0.0))
            vat_amt = float(order_data.get('vat_amount', 0.0))
            lines.append(f"VATable Sales: {vatable:.2f}".rjust(cols))
            lines.append(f"12% VAT Amount: {vat_amt:.2f}".rjust(cols))

        lines.extend([
            sep,
            f"PTU NO: {self.ptu_number}".center(cols),
            f"PTU ISSUED: {self.ptu_date_issued}".center(cols),
            self.accreditation_disclaimer,
            sep,
            (self.merchant_footer_message or '').center(cols),
        ])
        return self._text_to_escpos_hex('\n'.join(lines), cut_paper=True)
