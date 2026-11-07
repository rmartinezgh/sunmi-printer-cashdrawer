import base64
import csv
import io
import zipfile
from datetime import datetime, time
from xml.sax.saxutils import escape
from odoo import api, fields, models, _


class PowerPosHardwareReportWizard(models.TransientModel):
    _name = 'powerpos.hardware.report.wizard'
    _description = 'PowerPOS Hardware & Cash Drawer Analytics Report Wizard'

    report_type = fields.Selection(
        selection=[
            ('daily_drawer_activity', '1. Daily Drawer Activity Report'),
            ('cashier_drawer_activity', '2. Cashier Drawer Activity Report'),
            ('drawer_opening_duration', '3. Drawer Opening Duration Report'),
            ('unmatched_drawer_opening', '4. Unmatched Drawer Opening Report'),
            ('supervisor_override', '5. Supervisor Override Report'),
            ('printer_performance', '6. Printer Performance Report'),
            ('device_connectivity', '7. Device Connectivity Report'),
            ('cash_drawer_exception', '8. Cash Drawer Exception Report'),
        ],
        string='Report Type',
        required=True,
        default='daily_drawer_activity',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
    )
    branch_id = fields.Many2one(
        'powerpos.branch',
        string='Branch Filter (Optional)',
        domain="[('company_id', '=', company_id)]",
    )
    cash_drawer_id = fields.Many2one(
        'powerpos.cashdrawer.device',
        string='Cash Drawer Filter (Optional)',
        domain="[('company_id', '=', company_id)]",
    )
    cashier_id = fields.Many2one(
        'hr.employee',
        string='Cashier Filter (Optional)',
    )
    date_from = fields.Date(
        string='Start Date',
        required=True,
        default=fields.Date.context_today,
    )
    date_to = fields.Date(
        string='End Date',
        required=True,
        default=fields.Date.context_today,
    )

    export_file = fields.Binary(string='Generated Export File', readonly=True)
    export_filename = fields.Char(string='Export Filename', readonly=True)

    def _get_datetime_range(self):
        self.ensure_one()
        dt_start = datetime.combine(self.date_from, time.min)
        dt_end = datetime.combine(self.date_to, time.max)
        return fields.Datetime.to_string(dt_start), fields.Datetime.to_string(dt_end)

    def get_report_dataset(self):
        """Builds structured headers, rows, and summary KPIs for all 8 required reports."""
        self.ensure_one()
        dt_from, dt_to = self._get_datetime_range()
        Event = self.env['powerpos.cashdrawer.event']
        PrintJob = self.env['powerpos.sunmi.print.job']
        Device = self.env['powerpos.sunmi.device']

        ev_dom = [
            ('company_id', '=', self.company_id.id),
            ('device_observed_timestamp', '>=', dt_from),
            ('device_observed_timestamp', '<=', dt_to),
        ]
        if self.branch_id:
            ev_dom.append(('branch_id', '=', self.branch_id.id))
        if self.cash_drawer_id:
            ev_dom.append(('cash_drawer_id', '=', self.cash_drawer_id.id))
        if self.cashier_id:
            ev_dom.append(('cashier_id', '=', self.cashier_id.id))

        headers = []
        rows = []
        summary = {}

        if self.report_type == 'daily_drawer_activity':
            headers = ['Timestamp', 'Branch', 'POS Register', 'Cash Drawer', 'Event Type', 'Reason', 'Cashier', 'Duration (s)', 'Hash Prefix']
            events = Event.search(ev_dom, order='device_observed_timestamp asc')
            for ev in events:
                rows.append([
                    fields.Datetime.to_string(ev.device_observed_timestamp),
                    ev.branch_id.name or '',
                    ev.pos_config_id.name or '',
                    ev.cash_drawer_id.name or '',
                    ev.event_type,
                    ev.opening_reason or '',
                    ev.cashier_id.name or (ev.user_id.name if ev.user_id else ''),
                    f"{ev.opening_duration:.1f}",
                    ev.event_hash[:12],
                ])
            summary = {
                'Total Events': len(events),
                'Verified Openings': len(events.filtered(lambda e: e.new_physical_state == 'OPEN')),
                'Unmatched Openings': len(events.filtered(lambda e: e.event_type == 'unmatched_opening')),
            }

        elif self.report_type == 'cashier_drawer_activity':
            headers = ['Cashier', 'Branch', 'Total Openings', 'Sale Auto-Opens', 'Manual / Cash-In/Out', 'Supervisor Overrides', 'Unmatched Openings', 'Avg Duration (s)']
            events = Event.search(ev_dom)
            grouped = {}
            for ev in events:
                key = (ev.cashier_id.name or ev.user_id.name or _('Unassigned'), ev.branch_id.name or '')
                bucket = grouped.setdefault(key, {'openings': 0, 'sale': 0, 'manual': 0, 'sup': 0, 'unmatched': 0, 'durations': []})
                if ev.event_type in ('drawer_opened', 'sale_auto_open', 'manual_open_authorized', 'supervisor_override_open', 'unmatched_opening'):
                    bucket['openings'] += 1
                if ev.event_type == 'sale_auto_open':
                    bucket['sale'] += 1
                elif ev.event_type == 'manual_open_authorized':
                    bucket['manual'] += 1
                elif ev.event_type == 'supervisor_override_open':
                    bucket['sup'] += 1
                elif ev.event_type == 'unmatched_opening':
                    bucket['unmatched'] += 1
                if ev.opening_duration > 0:
                    bucket['durations'].append(ev.opening_duration)

            for (cashier_name, branch_name), stats in grouped.items():
                avg_dur = sum(stats['durations']) / len(stats['durations']) if stats['durations'] else 0.0
                rows.append([
                    cashier_name,
                    branch_name,
                    str(stats['openings']),
                    str(stats['sale']),
                    str(stats['manual']),
                    str(stats['sup']),
                    str(stats['unmatched']),
                    f"{avg_dur:.1f}",
                ])
            summary = {'Cashiers Analyzed': len(rows)}

        elif self.report_type == 'drawer_opening_duration':
            headers = ['Timestamp', 'Branch', 'Cash Drawer', 'Cashier', 'Opening Duration (s)', 'Incomplete Telemetry', 'Threshold Exceeded', 'Review Status']
            events = Event.search(ev_dom + [('event_type', 'in', ('drawer_closed', 'drawer_open_timeout'))], order='opening_duration desc')
            for ev in events:
                threshold = ev.cash_drawer_id.alert_prolonged_open_seconds or 30
                rows.append([
                    fields.Datetime.to_string(ev.device_observed_timestamp),
                    ev.branch_id.name or '',
                    ev.cash_drawer_id.name or '',
                    ev.cashier_id.name or (ev.user_id.name if ev.user_id else ''),
                    f"{ev.opening_duration:.2f}",
                    'YES' if ev.is_duration_incomplete else 'NO',
                    'YES' if ev.opening_duration >= threshold else 'NO',
                    ev.review_status,
                ])
            durations = [e.opening_duration for e in events if e.opening_duration > 0]
            summary = {
                'Closed/Timed-Out Events': len(events),
                'Max Duration (s)': f"{max(durations):.1f}" if durations else '0.0',
                'Average Duration (s)': f"{(sum(durations) / len(durations)):.1f}" if durations else '0.0',
            }

        elif self.report_type == 'unmatched_drawer_opening':
            headers = ['Timestamp', 'Event UUID', 'Branch', 'POS Register', 'Cash Drawer', 'Cashier on Duty', 'POS Session', 'Review Status', 'Supervisor Notes']
            events = Event.search(ev_dom + [('event_type', '=', 'unmatched_opening')], order='device_observed_timestamp desc')
            for ev in events:
                rows.append([
                    fields.Datetime.to_string(ev.device_observed_timestamp),
                    ev.event_uuid,
                    ev.branch_id.name or '',
                    ev.pos_config_id.name or '',
                    ev.cash_drawer_id.name or '',
                    ev.cashier_id.name or '',
                    ev.pos_session_id.name or 'OUTSIDE SESSION',
                    ev.review_status,
                    ev.review_notes or '',
                ])
            summary = {
                'Total Unmatched Openings': len(events),
                'Pending Supervisor Review': len(events.filtered(lambda e: e.review_status == 'pending_review')),
            }

        elif self.report_type == 'supervisor_override':
            headers = ['Timestamp', 'Branch', 'Cash Drawer', 'Requesting User', 'Approving Supervisor', 'Reason', 'Notes', 'Auth UUID']
            auth_dom = [
                ('company_id', '=', self.company_id.id),
                ('requested_at', '>=', dt_from),
                ('requested_at', '<=', dt_to),
                ('supervisor_id', '!=', False),
            ]
            if self.branch_id:
                auth_dom.append(('branch_id', '=', self.branch_id.id))
            auths = self.env['powerpos.cashdrawer.authorization'].search(auth_dom, order='requested_at desc')
            for auth in auths:
                rows.append([
                    fields.Datetime.to_string(auth.requested_at),
                    auth.branch_id.name or '',
                    auth.drawer_id.name or '',
                    auth.user_id.name or '',
                    auth.supervisor_id.name or '',
                    auth.reason,
                    auth.notes or '',
                    auth.auth_uuid,
                ])
            summary = {'Total Supervisor Overrides': len(auths)}

        elif self.report_type == 'printer_performance':
            headers = ['Job UUID', 'Submitted At', 'Branch', 'SUNMI Printer', 'Receipt Type', 'State', 'Retries', 'Error / Notes']
            job_dom = [
                ('company_id', '=', self.company_id.id),
                ('create_date', '>=', dt_from),
                ('create_date', '<=', dt_to),
            ]
            if self.branch_id:
                job_dom.append(('branch_id', '=', self.branch_id.id))
            jobs = PrintJob.search(job_dom, order='create_date desc')
            for job in jobs:
                rows.append([
                    job.job_uuid,
                    fields.Datetime.to_string(job.submitted_at or job.create_date),
                    job.branch_id.name or '',
                    job.device_id.name or '',
                    job.receipt_type,
                    job.state,
                    str(job.retry_count),
                    job.error_message or '',
                ])
            summary = {
                'Total Print Jobs': len(jobs),
                'Printed / Accepted': len(jobs.filtered(lambda j: j.state in ('printed', 'accepted'))),
                'Failed Jobs': len(jobs.filtered(lambda j: j.state == 'failed')),
            }

        elif self.report_type == 'device_connectivity':
            headers = ['Device ID', 'Device Name', 'Serial No.', 'Branch', 'Model', 'Connection Type', 'Capability Classification', 'Device Status', 'Last Heartbeat']
            dev_dom = [('company_id', '=', self.company_id.id)]
            if self.branch_id:
                dev_dom.append(('branch_id', '=', self.branch_id.id))
            devices = Device.search(dev_dom)
            for dev in devices:
                rows.append([
                    dev.device_code,
                    dev.name,
                    dev.serial_number,
                    dev.branch_id.name or '',
                    dev.device_model,
                    dev.connection_type,
                    dev.capability_classification,
                    dev.device_status,
                    fields.Datetime.to_string(dev.last_heartbeat) if dev.last_heartbeat else 'Never',
                ])
            summary = {
                'Total Devices': len(devices),
                'Online': len(devices.filtered(lambda d: d.device_status == 'online')),
                'Offline / Error': len(devices.filtered(lambda d: d.device_status in ('offline', 'error'))),
            }

        elif self.report_type == 'cash_drawer_exception':
            headers = ['Timestamp', 'Branch', 'Cash Drawer', 'Exception Type', 'Duration (s)', 'Incomplete Flag', 'Review Status', 'Supervisor', 'Notes']
            exc_dom = ev_dom + [('event_type', 'in', ('unmatched_opening', 'drawer_open_timeout', 'drawer_open_failed', 'sensor_error', 'device_offline'))]
            events = Event.search(exc_dom, order='device_observed_timestamp desc')
            for ev in events:
                rows.append([
                    fields.Datetime.to_string(ev.device_observed_timestamp),
                    ev.branch_id.name or '',
                    ev.cash_drawer_id.name or '',
                    ev.event_type,
                    f"{ev.opening_duration:.1f}",
                    'YES' if ev.is_duration_incomplete else 'NO',
                    ev.review_status,
                    ev.supervisor_id.name or '',
                    ev.review_notes or '',
                ])
            summary = {
                'Total Exception Events': len(events),
                'Unresolved Exceptions': len(events.filtered(lambda e: e.review_status in ('pending_review', 'under_investigation'))),
            }

        report_label = dict(self._fields['report_type'].selection).get(self.report_type, self.report_type)
        return {
            'title': report_label,
            'report_type': self.report_type,
            'date_from': fields.Date.to_string(self.date_from),
            'date_to': fields.Date.to_string(self.date_to),
            'branch_name': self.branch_id.name or _('All Branches'),
            'headers': headers,
            'rows': rows,
            'summary': summary,
        }

    def action_print_pdf(self):
        self.ensure_one()
        return self.env.ref('powerpos_sunmi_reports.action_report_powerpos_hardware_pdf').report_action(self)

    def action_export_csv(self):
        self.ensure_one()
        dataset = self.get_report_dataset()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([dataset['title']])
        writer.writerow(['Period', f"{dataset['date_from']} to {dataset['date_to']}", 'Branch', dataset['branch_name']])
        writer.writerow([])
        writer.writerow(dataset['headers'])
        for row in dataset['rows']:
            writer.writerow(row)
        csv_bytes = buf.getvalue().encode('utf-8-sig')
        filename = f"powerpos_{self.report_type}_{self.date_from}_{self.date_to}.csv"
        self.write({
            'export_file': base64.b64encode(csv_bytes),
            'export_filename': filename,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @api.model
    def _build_openxml_xlsx_bytes(self, sheet_title, headers, rows):
        """Generates a valid OpenXML .xlsx workbook using Python's standard zipfile library (zero external dependencies)."""
        content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
        rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
        wb_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
        safe_title = escape((sheet_title or 'Report')[:31])
        workbook = f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="{safe_title}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""

        all_rows = [headers] + rows
        row_xml_list = []
        for r_idx, row_vals in enumerate(all_rows, start=1):
            cells_xml = []
            for val in row_vals:
                escaped_val = escape(str(val if val is not None else ''))
                cells_xml.append(f'<c t="inlineStr"><is><t>{escaped_val}</t></is></c>')
            row_xml_list.append(f'<row r="{r_idx}">{"".join(cells_xml)}</row>')

        sheet_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    {"".join(row_xml_list)}
  </sheetData>
</worksheet>"""

        out = io.BytesIO()
        with zipfile.ZipFile(out, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('[Content_Types].xml', content_types)
            zf.writestr('_rels/.rels', rels)
            zf.writestr('xl/_rels/workbook.xml.rels', wb_rels)
            zf.writestr('xl/workbook.xml', workbook)
            zf.writestr('xl/worksheets/sheet1.xml', sheet_xml)
        return out.getvalue()

    def action_export_xlsx(self):
        self.ensure_one()
        dataset = self.get_report_dataset()
        xlsx_bytes = self._build_openxml_xlsx_bytes(
            sheet_title=dataset['title'],
            headers=dataset['headers'],
            rows=dataset['rows'],
        )
        filename = f"powerpos_{self.report_type}_{self.date_from}_{self.date_to}.xlsx"
        self.write({
            'export_file': base64.b64encode(xlsx_bytes),
            'export_filename': filename,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
