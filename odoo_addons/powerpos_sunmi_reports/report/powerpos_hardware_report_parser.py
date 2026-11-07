from odoo import api, models


class PowerPosHardwareReportParser(models.AbstractModel):
    _name = 'report.powerpos_sunmi_reports.report_powerpos_hardware_doc'
    _description = 'PowerPOS Hardware & Cash Drawer QWeb PDF Report Parser'

    @api.model
    def _get_report_values(self, docids, data=None):
        wizards = self.env['powerpos.hardware.report.wizard'].browse(docids)
        datasets = [wiz.get_report_dataset() for wiz in wizards]
        return {
            'doc_ids': docids,
            'doc_model': 'powerpos.hardware.report.wizard',
            'docs': wizards,
            'datasets': datasets,
        }
