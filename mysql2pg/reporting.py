"""
HTML migration report generation.
"""


def generate_html_report(validation: dict, diff: dict, mysql_db: str, pg_db: str):
    """Generate a detailed HTML migration report."""
    html_file = "migration_report.html"
    
    # CSS for a modern look
    css = """
    body { font-family: 'Inter', -apple-system, sans-serif; line-height: 1.5; color: #333; max-width: 1200px; margin: 0 auto; padding: 40px 20px; background-color: #f8f9fa; }
    h1, h2, h3 { color: #1a202c; }
    .header { border-bottom: 2px solid #e2e8f0; padding-bottom: 20px; margin-bottom: 40px; display: flex; justify-content: space-between; align-items: center; }
    .status { padding: 8px 16px; border-radius: 9999px; font-weight: 600; font-size: 0.875rem; }
    .status-pass { background-color: #c6f6d5; color: #22543d; }
    .status-fail { background-color: #fed7d7; color: #822727; }
    .card { background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 24px; margin-bottom: 32px; }
    table { width: 100%; border-collapse: collapse; margin-top: 16px; }
    th { text-align: left; padding: 12px; background: #f7fafc; border-bottom: 2px solid #edf2f7; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #4a5568; }
    td { padding: 12px; border-bottom: 1px solid #edf2f7; font-size: 0.875rem; }
    .table-name { font-weight: 600; color: #2d3748; }
    .mysql-val { color: #b7791f; }
    .pg-val { color: #2f855a; }
    .badge { padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
    .badge-ok { background: #c6f6d5; color: #22543d; }
    .badge-err { background: #fed7d7; color: #822727; }
    .badge-warn { background: #feebc8; color: #744210; }
    .conversion-tag { color: #718096; font-style: italic; }
    """

    status_class = "status-pass" if validation["all_passed"] else "status-fail"
    status_text = "PASSED" if validation["all_passed"] else "ISSUES DETECTED"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Migration Report - {mysql_db} to {pg_db}</title>
    <style>{css}</style>
</head>
<body>
    <div class="header">
        <div>
            <h1>Migration Report</h1>
            <p style="color: #718096; margin-top: 4px;">{mysql_db} (MySQL) &rarr; {pg_db} (PostgreSQL)</p>
        </div>
        <div class="status {status_class}">{status_text}</div>
    </div>

    <!-- Summary Stats -->
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 40px;">
        <div class="card" style="margin-bottom: 0; text-align: center;">
            <div style="color: #718096; font-size: 0.875rem;">Tables Migrated</div>
            <div style="font-size: 2rem; font-weight: 700; color: #2d3748;">{validation['row_counts']['passed']}/{validation['row_counts']['total']}</div>
        </div>
        <div class="card" style="margin-bottom: 0; text-align: center;">
            <div style="color: #718096; font-size: 0.875rem;">Type Conversions</div>
            <div style="font-size: 2rem; font-weight: 700; color: #2d3748;">{diff['conversions']}</div>
        </div>
        <div class="card" style="margin-bottom: 0; text-align: center;">
            <div style="color: #718096; font-size: 0.875rem;">Objects Verified</div>
            <div style="font-size: 2rem; font-weight: 700; color: #2d3748;">{len(validation['constraints'].get('indexes', [])) + len(validation['constraints'].get('foreign_keys', []))}</div>
        </div>
    </div>

    {f'<div class="card" style="border-left: 4px solid #f56565;"><h3>Errors</h3><ul style="color: #c53030;">' + "".join(f"<li>{e}</li>" for e in validation['validation_errors']) + '</ul></div>' if validation['validation_errors'] else ""}

    <div class="card">
        <h3>Row Count Comparison</h3>
        <table>
            <thead>
                <tr>
                    <th>Table Name</th>
                    <th>MySQL Count</th>
                    <th>PostgreSQL Count</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
    """

    for t in validation['row_counts']['tables']:
        b_class = "badge-ok" if "OK" in t['status'] else "badge-err"
        if "EXTRA" in t['status']: b_class = "badge-warn"
        
        html += f"""
                <tr>
                    <td class="table-name">{t['table']}</td>
                    <td class="mysql-val">{t['mysql']}</td>
                    <td class="pg-val">{t['pg']}</td>
                    <td><span class="badge {b_class}">{t['status']}</span></td>
                </tr>"""

    html += """
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>Type Mappings &amp; Conversions</h3>
        <table>
            <thead>
                <tr>
                    <th>Table.Column</th>
                    <th>MySQL Type</th>
                    <th>PostgreSQL Type</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
    """

    for d in diff['diffs']:
        if d['status'] == 'identical': continue
        b_class = "badge-ok" if d['status'] == 'converted' else "badge-warn"
        if d['status'] == 'missing': b_class = "badge-err"
        
        html += f"""
                <tr>
                    <td class="table-name">{d['key']}</td>
                    <td class="mysql-val">{d['mysql']}</td>
                    <td class="pg-val">{d['pg']}</td>
                    <td><span class="badge {b_class}">{d['status']}</span></td>
                </tr>"""

    html += """
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>Constraints &amp; Metadata</h3>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 40px;">
            <div>
                <h4>Foreign Keys</h4>
                <ul style="font-size: 0.875rem; color: #4a5568;">
    """
    
    if validation['constraints'].get('foreign_keys'):
        for fk in validation['constraints']['foreign_keys']:
            html += f"<li><code>{fk[0]}.{fk[1]}</code> &rarr; <code>{fk[2]}.{fk[3]}</code></li>"
    else:
        html += "<li>No foreign keys detected</li>"

    html += """
                </ul>
            </div>
            <div>
                <h4>Indexes</h4>
                <ul style="font-size: 0.875rem; color: #4a5568;">
    """

    if validation['constraints'].get('indexes'):
        for idx in validation['constraints']['indexes']:
            html += f"<li><code>{idx[1]}</code> on <code>{idx[0]}</code></li>"
    else:
        html += "<li>No indexes detected</li>"

    html += """
                </ul>
            </div>
        </div>
    </div>

    <footer style="text-align: center; color: #a0aec0; font-size: 0.75rem; margin-top: 40px;">
        Generated by mysql2pg migration tool
    </footer>
</body>
</html>
    """

    with open(html_file, "w") as f:
        f.write(html)
    
    return html_file
