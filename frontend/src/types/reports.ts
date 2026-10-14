export type ReportKind = 'ATTENDANCE' | 'LEAVE' | 'ATTENDANCE_FIX' | 'APPROVAL_QUEUE' | 'HEADCOUNT' | 'MY_PAYSLIP' | 'PAYROLL_SUMMARY';
export type ReportScope = 'SELF' | 'DIRECT_REPORTS' | 'COMPANY';
export interface ReportInputs { kind: ReportKind; start_date: string; end_date: string; scope?: ReportScope; department_id?: number; employee_id?: number }
export interface Report { kind: ReportKind; start_date: string; end_date: string; scope?: ReportScope; note: string; rows: Record<string, string | number | null>[] }
export interface ReportRun {
  run_id: string; kind: ReportKind; scope: ReportScope; status: 'GENERATING' | 'READY' | 'FAILED' | 'EXPIRED';
  error_code?: string | null; row_count: number | null; template_version: string;
  as_of: string | null; created_at: string; expires_at: string;
}
export interface ReportDefinition {
  kind: ReportKind; label: string; scopes: ReportScope[]; default_scope: ReportScope;
  filters: string[]; columns: string[]; template_version: string; period_rule: string;
  released_statuses: string[]; max_rows: number;
}
export interface ReportRunCreateResponse { run: ReportRun; report: Report }
export interface ReportRunPage { items: ReportRun[]; next_cursor: string | null }
