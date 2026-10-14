import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Space, Spin, Switch, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import api from '../api/client';

interface ScopeRow {
  role: string;
  scopes: string[];
  maximum: string[];
  editable: boolean;
}

const labels: Record<string, string> = {
  ADMIN: 'Quản trị viên',
  HR: 'Nhân sự',
  MANAGER: 'Quản lý trực tiếp',
  EMPLOYEE: 'Nhân viên',
};

export const AIDataAccessPage: React.FC = () => {
  const [rows, setRows] = useState<ScopeRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await api.get<ScopeRow[]>('/ai/access/roles');
      setRows(res.data);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const setScope = async (row: ScopeRow, scope: string, enabled: boolean) => {
    if (!row.editable || !row.maximum.includes(scope) || saving) return;
    const updated = new Set(row.scopes);
    if (enabled) updated.add(scope);
    else updated.delete(scope);
    setSaving(row.role);
    try {
      const response = await api.put<ScopeRow>(`/ai/access/roles/${row.role}`, {
        scopes: [...updated],
      });
      setRows((old) => old.map((item) => item.role === row.role ? response.data : item));
      message.success('Đã cập nhật quyền truy cập AI');
    } catch {
      message.error('Không thể lưu quyền. Hệ thống giữ nguyên cấu hình trước đó.');
    } finally {
      setSaving(null);
    }
  };

  const columns: ColumnsType<ScopeRow> = [
    { title: 'Vai trò HR', dataIndex: 'role', key: 'role',
      render: (role: string) => <Space direction="vertical" size={2}><strong>{labels[role] || role}</strong>
        <Typography.Text type="secondary">{role}</Typography.Text></Space> },
    { title: 'Dữ liệu cá nhân', key: 'self',
      render: () => <Tag color="green">Luôn cho phép (SELF)</Tag> },
    ...([
      { key: 'DIRECT_REPORTS', title: 'Nhân viên trực tiếp' },
      { key: 'COMPANY', title: 'Toàn công ty' },
    ] as const).map(({ key, title }) => ({
      key, title,
      render: (_: unknown, row: ScopeRow) => row.maximum.includes(key)
        ? <Switch aria-label={`${row.role}: ${title}`} checked={row.scopes.includes(key)}
            disabled={!row.editable || saving !== null} loading={saving === row.role}
            onChange={(value) => void setScope(row, key, value)} />
        : <Typography.Text type="secondary">Không thuộc quyền HR</Typography.Text>,
    })),
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="large">
      <div>
        <Typography.Title level={3}>Phân quyền dữ liệu cho AI Chatbox</Typography.Title>
        <Typography.Text type="secondary">Chính sách riêng của AI, được thực thi ở API. Thay đổi có hiệu lực từ request tiếp theo.</Typography.Text>
      </div>
      <Alert type="info" showIcon message="Quyền cơ sở không thay đổi"
        description="Mỗi người luôn được truy cập dữ liệu cá nhân của chính họ. Quản trị viên chỉ có thể thu hẹp phạm vi mà role HR hiện có cho phép. Không thể dùng bảng này để cấp dữ liệu toàn công ty cho tài khoản chỉ có role EMPLOYEE/MANAGER." />
      {error ? <Alert type="error" showIcon message="Không tải được chính sách"
        action={<Button onClick={() => void load()}>Thử lại</Button>} /> : null}
      <Card>
        {loading ? <Spin /> : <Table<ScopeRow> rowKey="role" columns={columns}
          dataSource={rows} pagination={false} scroll={{ x: 650 }} />}
      </Card>
      <Typography.Paragraph type="secondary">
        Giới hạn này áp dụng cho công cụ AI. Phân quyền trang nhân sự, báo cáo thông thường và quyền xem tài liệu công bố vẫn tuân theo hệ thống HR gốc. Người dùng không thể yêu cầu AI bỏ qua phân quyền để đọc dữ liệu người khác.
      </Typography.Paragraph>
    </Space>
  );
};
